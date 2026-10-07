#!/usr/bin/env bash
# Deploy SciToolAgent's ToolsAgent service, at the commit the adapter was reviewed against,
# with the Chemical category's dependencies, listening on 127.0.0.1 only.
#
#     scripts/deploy_toolsagent.sh DIR [PORT]
#
# DIR receives the clone, a virtual environment and service.log. The script returns once
# the service answers, and prints the TOOLSAGENT_URL and TOOLSAGENT_CATEGORIES that
# tests/test_toolsagent.py reads. The Biology and Material categories need PyTorch,
# transformers and model weights; they are left out, so their functions answer
# UNAVAILABLE (docs/compute-tasks.md). Upstream pins numpy 2.1.3 beside langchain 0.3.8,
# which requires numpy<2, and rdkit 2023.9.3, which predates numpy 2; the versions below
# install together, and IPython is added because rdkit's IPythonConsole, which the
# Chemical tools import, needs it.
set -euo pipefail

DIR=${1:?usage: deploy_toolsagent.sh DIR [PORT]}
PORT=${2:-60002}
PYTHON=${PYTHON:-python3}
COMMIT=ac1cf19fcef84e69f149db6da424afe4e9b2f11f

mkdir -p "$DIR"
DIR=$(cd "$DIR" && pwd)
if [ ! -d "$DIR/SciToolAgent/.git" ]; then
    git init -q "$DIR/SciToolAgent"
    git -C "$DIR/SciToolAgent" fetch -q --depth 1 \
        https://github.com/HICAI-ZJU/SciToolAgent "$COMMIT"
    git -C "$DIR/SciToolAgent" checkout -q FETCH_HEAD
fi
test "$(git -C "$DIR/SciToolAgent" rev-parse HEAD)" = "$COMMIT"

"$PYTHON" -m venv "$DIR/venv"
"$DIR/venv/bin/python" -m pip install -q --upgrade pip
"$DIR/venv/bin/python" -m pip install -q \
    "langchain==0.3.8" "langchain-community==0.3.8" "fastapi==0.115.5" "uvicorn==0.32.1" \
    "rxn4chemistry==1.13.1" "molbloom==3.2.0" "selfies==2.1.1" "tiktoken==0.14.0" \
    "html2text==2020.1.16" "scikit-learn==1.5.2" "pandas==2.2.3" "numpy==1.26.4" \
    "rdkit==2024.9.6" "python-dotenv==1.0.1" "requests==2.32.3" "ipython==9.17.1"

cd "$DIR/SciToolAgent/ToolsAgent"
"$DIR/venv/bin/python" -c "import ToolsFuns.Chemical.tool_name_dict" 2>/dev/null \
    || { echo "the Chemical tools do not import" >&2; exit 1; }
nohup "$DIR/venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" \
    > "$DIR/service.log" 2>&1 &
echo $! > "$DIR/service.pid"
for _ in $(seq 60); do
    if curl -sf --noproxy '*' "http://127.0.0.1:$PORT/docs" > /dev/null; then
        echo "TOOLSAGENT_URL=http://127.0.0.1:$PORT"
        echo "TOOLSAGENT_CATEGORIES=Chemical"
        exit 0
    fi
    sleep 1
done
echo "ToolsAgent did not answer on port $PORT; see $DIR/service.log" >&2
exit 1
