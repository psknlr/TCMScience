"""Declarative execution policy with self-testing rules.

Borrowed from Codex (`codex-rs/execpolicy/README.md`, `src/policy.rs`), with one property
from Claude Code (`docs/en/permissions`). Attribution and the reasons for each choice are in
`PATTERN_ATTRIBUTION.md`; the mechanics are here.

Three properties transfer from Codex's ``prefix_rule``:

* **Three decisions, not two.** ``allow`` / ``prompt`` / ``forbidden``. psh's tool gateway had
  a hardcoded destructive-command denylist — binary, and editable only by changing code.
* **Justification is a field.** A rule says why it exists, and a ``forbidden`` rule is asked to
  name an alternative. The justification is surfaced in the refusal.
* **Rules carry their own tests.** ``match`` and ``not_match`` are example invocations
  validated when the policy loads. A rule that does not match its own examples, or matches
  one it says it should not, is rejected before it can be applied. Codex calls these unit
  tests; that is what they are.

One property transfers from Claude Code's permission rules, with a recorded departure:

* **``forbidden`` is absolute: first match wins and specificity does not reorder.** A broad
  ``forbidden`` cannot be punched through by a narrower ``allow``. This is what makes the
  policy safe to extend: an amendment (see ``approvals.py``) can only ever add ``allow``
  rules, and every ``forbidden`` still overrides them.
* **Between ``prompt`` and ``allow``, the most specific match wins.** This departs from Claude
  Code, where ``ask`` also beats a narrower ``allow``. The departure is deliberate: the point
  of an approval amendment is that "ask before ``git push``" plus a user saying "allow
  ``git push origin`` from now on" must actually stop asking. Under strict ask-before-allow it
  never would — an amendment could only fill an *unruled* gap, which is not the case it exists
  for. Codex's ``ApprovedExecpolicyAmendment`` presupposes that a prompted command becomes
  allowed, so where the two sources disagree, this follows Codex. The safety property that
  matters — nothing relaxes a ``forbidden`` — is unchanged.

Not borrowed: Starlark. Rules are dataclasses and load from JSON. A second language inside a
small kernel is cost without benefit at this scale.

Matching model
--------------
A command is a token list (``shlex``-split when given as a string). A pattern is an ordered
list of tokens; each element is either a literal string or a list of alternatives, and ``*``
matches any single token. A pattern matches a command when the command *begins with* the
pattern — a prefix rule, exactly as in Codex.

What a rule is matched against is every command the line would run, not only its first
word. Matching the raw prefix alone let the shipped "read-only inspection" rule allow
``find . -delete``, ``find . -exec sh -c '…' ;`` and ``echo hi > /etc/passwd`` with no
approval, and let ``/usr/bin/sudo``, ``env sudo`` and ``rm --recursive --force`` reach the
PROMPT default instead of their forbidden rules. So a command is first analysed
(``_analyse``): a string is split at shell operators (``;``, ``&&``, ``|``, newlines, …)
where the shell reads them — unquoted and unescaped, with comments read as comments — so
``find . '|' ls -delete`` is the one ``find`` the shell runs, not two commands. Each
command loses its wrappers (``env``, ``nice``, ``xargs``, ``timeout``, …), leading
assignments and the directory on its name, and ``sh -c`` strings, command substitutions and
``find -exec`` commands are analysed as commands of their own. Forbidden rules are checked
against every one of them, rm's recursive flags in any position or abbreviation included.
A rule that allows a command allows it only under the name it was written for, so ``./ls``
and ``env X=… ls`` are asked about, not allowed by the ``ls`` rule. And the shapes a prefix
rule cannot describe are always asked about: a ``find`` that deletes, writes or runs
(``-delete``, ``-exec``, ``-ok``, ``-fprint``, ``-fls``; checked a second time over the whole
line, and through braces and globs that could expand into one), an output redirection into
a file, a here-document, and an expansion or substitution whose value the policy cannot see.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..contracts import PolicyDenied

__all__ = ["Decision", "PrefixRule", "ExecPolicy", "PolicyEvaluation", "DEFAULT_RULES",
           "RuleValidationError"]


class Decision(str, Enum):
    ALLOW = "allow"
    PROMPT = "prompt"
    FORBIDDEN = "forbidden"

    @property
    def rank(self) -> int:
        # Evaluation order: forbidden first, then prompt, then allow.
        return {"forbidden": 0, "prompt": 1, "allow": 2}[self.value]


class RuleValidationError(PolicyDenied):
    """A rule failed its own examples at load time."""


Token = str | Sequence[str]


def _tokenize(command: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(command, str):
        return tuple(shlex.split(command))
    return tuple(str(t) for t in command)


@dataclass(frozen=True, slots=True)
class PrefixRule:
    """One rule: a token-prefix pattern and what to do when a command matches it."""

    pattern: tuple[Token, ...]
    decision: Decision = Decision.ALLOW
    justification: str = ""
    match: tuple[tuple[str, ...], ...] = ()
    not_match: tuple[tuple[str, ...], ...] = ()
    #: Where the rule came from: "default", "policy_file", "amendment:<session>" ...
    origin: str = "default"

    def __post_init__(self) -> None:
        if not self.pattern:
            raise RuleValidationError("a rule needs at least one pattern token")
        if self.decision is Decision.FORBIDDEN and not self.justification:
            raise RuleValidationError(
                "a forbidden rule must carry a justification, ideally naming an alternative")
        # Self-test. A rule that fails its own examples is a rule nobody understands.
        for example in self.match:
            if not self.matches(example):
                raise RuleValidationError(
                    f"rule {self.render()} claims to match {list(example)!r} and does not")
        for example in self.not_match:
            if self.matches(example):
                raise RuleValidationError(
                    f"rule {self.render()} claims NOT to match {list(example)!r} and does")

    def matches(self, command: str | Sequence[str]) -> bool:
        tokens = _tokenize(command)
        if len(tokens) < len(self.pattern):
            return False
        for want, got in zip(self.pattern, tokens):
            if want == "*":
                continue
            if isinstance(want, str):
                if want != got:
                    return False
            elif got not in want:
                return False
        return True

    def render(self) -> str:
        return " ".join(f"({'|'.join(t)})" if not isinstance(t, str) else t
                        for t in self.pattern)

    def to_dict(self) -> dict[str, Any]:
        return {"pattern": [list(t) if not isinstance(t, str) else t for t in self.pattern],
                "decision": self.decision.value, "justification": self.justification,
                "match": [list(m) for m in self.match],
                "not_match": [list(m) for m in self.not_match], "origin": self.origin}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "PrefixRule":
        pattern = tuple(tuple(t) if isinstance(t, list) else str(t) for t in d["pattern"])
        return cls(pattern=pattern, decision=Decision(d.get("decision", "allow")),
                   justification=str(d.get("justification", "")),
                   match=tuple(_tokenize(m) for m in d.get("match", ())),
                   not_match=tuple(_tokenize(m) for m in d.get("not_match", ())),
                   origin=str(d.get("origin", "policy_file")))


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    decision: Decision
    rule: PrefixRule | None
    command: tuple[str, ...]

    @property
    def justification(self) -> str:
        return self.rule.justification if self.rule else ""

    def describe(self) -> str:
        if self.rule is None:
            return f"{self.decision.value}: no rule matched; default applies"
        why = f" — {self.rule.justification}" if self.rule.justification else ""
        return f"{self.decision.value} by rule [{self.rule.render()}]{why}"


#: The shipped default policy: psh v0.3's hardcoded denylist, now as rules with reasons.
DEFAULT_RULES: tuple[PrefixRule, ...] = (
    PrefixRule(("rm", ("-rf", "-fr", "-r", "-Rf", "-fR")), Decision.FORBIDDEN,
               "recursive delete is unrecoverable; move to a trash directory or delete "
               "specific files by name",
               match=(("rm", "-rf", "/"), ("rm", "-r", "build")), not_match=(("rm", "file.txt"),)),
    PrefixRule(("sudo",), Decision.FORBIDDEN,
               "privilege escalation is outside every profile; run the command without sudo "
               "or perform it manually",
               match=(("sudo", "apt", "install", "x"),), not_match=(("ls",),)),
    PrefixRule(("dd",), Decision.FORBIDDEN, "raw device writes; use a file-level tool",
               match=(("dd", "if=/dev/zero", "of=/dev/sda"),)),
    PrefixRule(("mkfs",), Decision.FORBIDDEN, "filesystem creation destroys the target",
               match=(("mkfs", "/dev/sda1"),)),
    PrefixRule((("shutdown", "reboot", "halt", "poweroff"),), Decision.FORBIDDEN,
               "system power state is not a research operation",
               match=(("shutdown", "-h", "now"), ("reboot",))),
    PrefixRule(("chmod", ("-R", "777")), Decision.FORBIDDEN,
               "world-writable recursive permissions; set permissions on the specific path",
               match=(("chmod", "-R", "777", "."), ("chmod", "777", "x")),
               not_match=(("chmod", "600", "key"),)),
    PrefixRule(("git", ("push", "reset", "checkout", "clean", "rebase")), Decision.PROMPT,
               "rewrites history or discards work; confirm before proceeding",
               match=(("git", "push", "--force"), ("git", "reset", "--hard")),
               not_match=(("git", "status"), ("git", "log"))),
    PrefixRule((("curl", "wget"),), Decision.PROMPT,
               "fetches remote content; the egress proxy governs the host, but confirm intent",
               match=(("curl", "https://x"),), not_match=(("cat", "f"),)),
    PrefixRule(("pip", ("install", "uninstall")), Decision.PROMPT,
               "changes the environment; confirm the package",
               match=(("pip", "install", "x"),), not_match=(("pip", "list"),)),
    PrefixRule((("ls", "cat", "head", "tail", "grep", "wc", "find", "echo", "pwd", "which"),),
               Decision.ALLOW, "read-only inspection",
               match=(("ls", "-la"), ("grep", "-rn", "x", ".")), not_match=(("rm", "x"),)),
    PrefixRule(("git", ("status", "log", "diff", "show", "branch")), Decision.ALLOW,
               "read-only repository inspection", match=(("git", "status"),)),
    PrefixRule(("python", ("-c", "-m")), Decision.PROMPT,
               "arbitrary code; confirm what it does", match=(("python", "-c", "print(1)"),),
               not_match=(("python", "--version"),)),
)


# ------------------------------------------------------------------ command analysis
#
# Not a shell. Enough of one to find every command a line runs, conservatively: where the
# analysis cannot tell what runs, the line is asked about rather than allowed.

#: Rules the analysis itself applies. Not in any rule list, so a policy file cannot drop
#: them, and only ever PROMPT: they can raise an ALLOW to a question, never relax anything.
_FIND_ACTION = PrefixRule(
    ("find",), Decision.PROMPT,
    "find with -delete, -exec, -execdir, -ok, -okdir, -fprint or -fls deletes or writes "
    "files or runs commands; it is not read-only inspection — confirm", origin="analysis")
_REDIRECT = PrefixRule(
    ("*",), Decision.PROMPT,
    "the command line redirects output into a file; a rule describes a command, not where "
    "its output is written — confirm", origin="analysis")
_EXPANSION = PrefixRule(
    ("*",), Decision.PROMPT,
    "the command line expands a variable or a brace pattern or substitutes a command, so "
    "what runs depends on a value the policy cannot see — confirm", origin="analysis")
_HEREDOC = PrefixRule(
    ("*",), Decision.PROMPT,
    "the command line holds a here-document, whose body the policy cannot tell apart from "
    "the commands around it — confirm", origin="analysis")
_RENAMED = PrefixRule(
    ("*",), Decision.PROMPT,
    "the command runs through a wrapper, an assignment or a path, and a rule allowing the "
    "bare name does not allow it — confirm", origin="analysis")
_TOO_DEEP = PrefixRule(
    ("*",), Decision.PROMPT,
    "commands nested this deep are not analysed — confirm", origin="analysis")

#: Commands that run the command named by their arguments, with their options that take a
#: separate argument. Stripped (repeatedly) to find the command that actually runs.
_WRAPPERS: dict[str, frozenset[str]] = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "command": frozenset(),
    "exec": frozenset({"-a"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "nohup": frozenset(),
    "setsid": frozenset(),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "xargs": frozenset({"-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-L",
                        "--max-lines", "-n", "--max-args", "-P", "--max-procs", "-s",
                        "--max-chars"}),
}
_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash", "fish"})
#: Shell words that may open a command without being one.
_RESERVED = frozenset({"{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done",
                       "while", "until", "case", "esac", "for", "select", "function"})
_FIND_RUNS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FIND_WRITES = frozenset({"-delete", "-fprint", "-fprint0", "-fprintf", "-fls"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_MAX_DEPTH = 8
#: The shell's operators, longest first. Each begins with one of its metacharacters
#: (``| & ; ( ) < >`` and newline), so an unquoted metacharacter always ends a word.
_OPERATORS = tuple(sorted(
    {"&&", "||", ";;&", ";;", ";&", "|&", "&>>", "&>", "<<<", "<<-", "<<", "<>", "<&", ">&",
     ">>", ">|", "<(", ">(", "<", ">", "|", "&", ";", "(", ")", "\n"}, key=len, reverse=True))


@dataclass(frozen=True, slots=True)
class _Token:
    """A word of a command line with its quotes removed, or one of the line's operators."""

    text: str
    operator: bool = False
    #: The word holds an unquoted or double-quoted ``$`` or backquote, or a brace pattern
    #: (``-{delete,name}``): the shell passes on something other than this text.
    expands: bool = False
    #: The word holds an unquoted ``*``, ``?`` or ``[``: the shell may replace it with the
    #: names of the files it matches.
    globs: bool = False


@dataclass(frozen=True, slots=True)
class _Command:
    """One command a line runs: as written, and as matched against forbidden rules."""

    literal: tuple[str, ...]
    argv: tuple[str, ...]


@dataclass(slots=True)
class _Analysis:
    commands: list[_Command] = field(default_factory=list)
    #: The first structural reason to ask, if any.
    prompt: PrefixRule | None = None

    def ask(self, rule: PrefixRule) -> None:
        if self.prompt is None:
            self.prompt = rule


def _analyse(command: str | Sequence[str]) -> _Analysis:
    out = _Analysis()
    if isinstance(command, str):
        _analyse_line(command, out, 0)
    else:
        _analyse_argv(tuple(str(t) for t in command), out, 0)
    return out


def _analyse_line(line: str, out: _Analysis, depth: int) -> None:
    """A command line a shell interprets: split at its operators, each command analysed."""
    if depth > _MAX_DEPTH:
        out.ask(_TOO_DEEP)
        return
    for match in _SUBSTITUTION.finditer(line):
        out.ask(_EXPANSION)
        _analyse_line(match.group(1) if match.group(1) is not None else match.group(2),
                      out, depth + 1)
    try:
        tokens = _lex(line)
    except ValueError:
        if depth == 0:
            raise                     # the line itself does not parse: as before, an error
        out.ask(_EXPANSION)           # a nested command that does not parse is asked about
        return
    _find_backstop(line, tokens, out)
    current: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token.operator:
            if token.expands:
                out.ask(_EXPANSION)
            current.append(token.text)
            continue
        op = token.text
        following = (tokens[index] if index < len(tokens) and not tokens[index].operator
                     else None)
        if op in ("<(", ">("):                        # <( … ) / >( … ): a command runs
            out.ask(_EXPANSION)
        elif op in ("<<", "<<-"):                     # a here-document and its delimiter
            out.ask(_HEREDOC)
            if following is not None:
                index += 1
            continue
        elif "<" in op or ">" in op:                  # a redirection and its target
            target = ""
            if following is not None:
                index += 1
                target = following.text
                if following.expands:
                    out.ask(_EXPANSION)
            if ">" in op and not (target == "/dev/null" or (
                    op.endswith("&") and (target.isdigit() or target == "-"))):
                out.ask(_REDIRECT)
            continue
        _analyse_argv(tuple(current), out, depth)
        current = []
    _analyse_argv(tuple(current), out, depth)


def _lex(line: str) -> list[_Token]:
    """Split a command line into words and operators as a POSIX shell does, quotes removed.

    An operator is one only where the shell reads one: unquoted and unescaped. A quoted or
    escaped ``;``, ``|``, ``&&`` or ``<`` is part of a word, and the shell hands it to the
    command as one, so ``find . '|' ls -delete`` is a single ``find`` that deletes. (shlex
    with ``punctuation_chars`` returns a quoted ``'|'`` exactly as it returns ``|``, which is
    how that line was allowed.) A ``#`` that begins a word comments out the rest of its line,
    as in the shell: read as a word, a quote inside a comment could hide the next line inside
    a quoted string. An unterminated quote raises ValueError, as it does in shlex.
    """
    tokens: list[_Token] = []
    chars: list[str] = []
    started = expands = globs = False
    brace = 0                         # 1: an unquoted "{" seen; 2: then a "," or ".."
    index, end = 0, len(line)

    def finish() -> None:
        nonlocal started, expands, globs, brace
        if started:
            tokens.append(_Token("".join(chars), expands=expands, globs=globs))
        chars.clear()
        started = expands = globs = False
        brace = 0

    def inside(position: int) -> str:
        """The character at ``position`` inside a quoted string, which must not end there."""
        if position >= end:
            raise ValueError("No closing quotation")
        return line[position]

    while index < end:
        ch = line[index]
        if ch in " \t":
            finish()
            index += 1
            continue
        if line.startswith("\\\n", index):           # a line continuation: nothing at all
            index += 2
            continue
        if ch == "#" and not started:                 # a comment, to the end of its line
            newline = line.find("\n", index)
            index = end if newline < 0 else newline
            continue
        op = (next(o for o in _OPERATORS if line.startswith(o, index))
              if ch in "|&;()<>\n" else None)
        if op is not None:
            finish()
            tokens.append(_Token(op, operator=True))
            index += len(op)
            continue
        started = True
        if ch == "\\":
            if index + 1 == end:
                raise ValueError("No escaped character")
            chars.append(line[index + 1])
            index += 2
        elif ch == "'":
            close = line.find("'", index + 1)
            if close < 0:
                raise ValueError("No closing quotation")
            chars.append(line[index + 1:close])
            index = close + 1
        elif ch == "$" and line.startswith("'", index + 1):
            # $'…': a backslash escapes the next character, a quote included. Not decoded,
            # so the word is an expansion; what matters is where the string ends.
            expands = True
            index += 2
            while inside(index) != "'":
                step = 2 if line[index] == "\\" else 1
                chars.append(line[index:index + step])
                index += step
            index += 1
        elif ch == '"':
            index += 1
            while (c := inside(index)) != '"':
                if c == "\\" and line[index + 1:index + 2] in ("$", "`", '"', "\\", "\n"):
                    if line[index + 1] != "\n":       # backslash-newline joins two lines
                        chars.append(line[index + 1])
                    index += 2
                    continue
                expands = expands or c in "$`"
                chars.append(c)
                index += 1
            index += 1
        else:
            if ch in "$`":
                expands = True
            elif ch in "*?[":
                globs = True
            elif ch == "{" and not brace:
                brace = 1
            elif brace == 1 and (ch == "," or line.startswith("..", index)):
                brace = 2
            elif ch == "}" and brace == 2:
                expands = True                        # {a,b} / {1..3}: brace expansion
            chars.append(ch)
            index += 1
    finish()
    return tokens


def _find_backstop(line: str, tokens: Sequence[_Token], out: _Analysis) -> None:
    """Ask about a line that names ``find`` and one of its actions, however the line splits.

    The command-by-command analysis decides; this is a second look that does not depend on
    it. If any word of the line is ``find`` (by any path) and any word is a ``find`` action,
    or a glob the shell could expand into one (``-delet?``, ``*`` beside a file named
    ``-delete``), the line is asked about — whichever command the action ended up in.
    """
    words = [t.text for t in tokens if not t.operator]
    views = [words]
    try:
        views.append(shlex.split(line))     # a second tokenizer, independent of _lex
    except ValueError:
        pass
    for view in views:
        if not any(w.rstrip("/").rsplit("/", 1)[-1] == "find" for w in view):
            continue
        if any(w in _FIND_RUNS or w in _FIND_WRITES
               or w.startswith(("-exec", "-ok", "-fprint")) for w in view):
            out.ask(_FIND_ACTION)
            return
        if any(t.globs and any(fnmatch.fnmatchcase(a, t.text) for a in (*_FIND_RUNS,
                                                                         *_FIND_WRITES))
               for t in tokens if not t.operator):
            out.ask(_FIND_ACTION)
            return


def _analyse_argv(literal: tuple[str, ...], out: _Analysis, depth: int) -> None:
    """One command: strip what runs it, record it, and analyse what it runs in turn."""
    if depth > _MAX_DEPTH:
        out.ask(_TOO_DEEP)
        return
    argv = list(literal)
    for _ in range(16):                       # bounded: wrappers may nest, not forever
        while argv and (argv[0] in _RESERVED or _ASSIGNMENT.match(argv[0])):
            argv.pop(0)
        if argv and "/" in argv[0]:
            argv[0] = argv[0].rstrip("/").rsplit("/", 1)[-1]
        if not argv or argv[0] not in _WRAPPERS:
            break
        argv = _strip_wrapper(argv)
    if not argv:
        return
    name = argv[0]
    if name == "rm" and _rm_is_recursive(argv):
        # Recursive in any position or spelling: -r, -R, -fr, -vrf, --recursive, after an
        # operand. Matched against the rules as the canonical "rm -r …".
        argv = ["rm", "-r", *[t for t in argv[1:] if not t.startswith("-")]]
    out.commands.append(_Command(literal=literal, argv=tuple(argv)))
    if name in _SHELLS:
        script = _shell_script(argv)
        if script is not None:
            _analyse_line(script, out, depth + 1)
    elif name == "find":
        index = 1
        while index < len(argv):
            token = argv[index]
            index += 1
            if token in _FIND_WRITES:
                out.ask(_FIND_ACTION)
            elif token in _FIND_RUNS:
                out.ask(_FIND_ACTION)
                end = index
                # ";" ends the command; "+" ends it only straight after "{}" (find(1)),
                # anywhere else it is an argument of the command.
                while end < len(argv) and not (argv[end] == ";" or (
                        argv[end] == "+" and argv[end - 1] == "{}")):
                    end += 1
                _analyse_argv(tuple(argv[index:end]), out, depth + 1)
                index = end + 1


def _strip_wrapper(argv: list[str]) -> list[str]:
    """Drop a wrapper and its options; what is left starts with the command it runs."""
    name, rest = argv[0], argv[1:]
    takes_argument = _WRAPPERS[name]
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--":
            index += 1
            break
        if name == "env" and token in ("-S", "--split-string") and index + 1 < len(rest):
            # env -S splits its argument into the command it runs.
            return _split(rest[index + 1]) + rest[index + 2:]
        if name == "env" and token.startswith("--split-string="):
            return _split(token.split("=", 1)[1]) + rest[index + 1:]
        if token.startswith("-") and len(token) > 1:
            index += 2 if token in takes_argument else 1
            continue
        if name == "env" and _ASSIGNMENT.match(token):
            index += 1
            continue
        break
    if name == "timeout" and index < len(rest):
        index += 1                            # the duration
    return rest[index:]


def _split(text: str) -> list[str]:
    """Split a string a wrapper runs; one that does not split is kept whole (no rule names it)."""
    try:
        return shlex.split(text)
    except ValueError:
        return [text]


def _rm_is_recursive(argv: Sequence[str]) -> bool:
    for token in argv[1:]:
        if token == "--":
            return False
        if token.startswith("--r") and "--recursive".startswith(token):
            return True                       # getopt takes any unambiguous prefix: --r
        if token.startswith("-") and not token.startswith("--") and (
                "r" in token or "R" in token):
            return True
    return False


def _shell_script(argv: Sequence[str]) -> str | None:
    """The command string a shell is told to run with -c, if it is told one."""
    seen_c = False
    tokens = iter(argv[1:])
    for token in tokens:
        if token in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            next(tokens, None)                # an option that takes an argument
            continue
        if len(token) > 1 and token[0] in "-+":
            seen_c = seen_c or (not token.startswith("--") and "c" in token[1:])
            continue
        return token if seen_c else None
    return None


class ExecPolicy:
    """An ordered rule set with forbidden → prompt → allow evaluation."""

    def __init__(self, rules: Iterable[PrefixRule] = DEFAULT_RULES, *,
                 default: Decision = Decision.PROMPT) -> None:
        self.rules: list[PrefixRule] = list(rules)
        #: What happens when no rule matches. PROMPT by default: an unknown command is asked
        #: about, not silently run and not silently refused.
        self.default = default
        self.evaluations = 0
        self.amendments = 0

    def evaluate(self, command: str | Sequence[str]) -> PolicyEvaluation:
        """Return the decision for a command. Forbidden rules are consulted first, always.

        The decision covers every command the line runs (see "Matching model"): forbidden
        if any of them is, otherwise the most cautious of their decisions, and a question
        where the analysis found a shape no rule describes.
        """
        self.evaluations += 1
        tokens = _tokenize(command)
        analysis = _analyse(command)
        # Forbidden first, absolutely: a forbidden rule is checked against every command
        # before any prompt or allow rule gets a say, and specificity is irrelevant.
        for each in analysis.commands:
            for rule in self.rules:
                if rule.decision is Decision.FORBIDDEN and (
                        rule.matches(each.argv) or rule.matches(each.literal)):
                    return PolicyEvaluation(Decision.FORBIDDEN, rule, tokens)
        decided: PolicyEvaluation | None = None
        for each in analysis.commands:
            decision, rule = self._decide(each)
            if decided is None or decision.rank < decided.decision.rank:
                decided = PolicyEvaluation(decision, rule, tokens)
        if decided is None:
            decided = PolicyEvaluation(self.default, None, tokens)
        if analysis.prompt is not None and decided.decision is Decision.ALLOW:
            return PolicyEvaluation(Decision.PROMPT, analysis.prompt, tokens)
        return decided

    def _decide(self, command: _Command) -> tuple[Decision, PrefixRule | None]:
        # Between prompt and allow, the most specific (longest) matching pattern wins, so an
        # amendment "allow git push origin" relaxes "prompt on git push" — but nothing here
        # can ever reach a command a forbidden rule already refused.
        best: PrefixRule | None = None
        as_written = False
        for rule in self.rules:
            if rule.decision is Decision.FORBIDDEN:
                continue
            literal = rule.matches(command.literal)
            if literal or rule.matches(command.argv):
                # >= : on equal specificity the LATER rule wins. Amendments are appended, so
                # a fresh "allow git push" beats the shipped "prompt git push" — the more
                # recent decision is the operative one.
                if best is None or len(rule.pattern) >= len(best.pattern):
                    best, as_written = rule, literal
        if best is None:
            return self.default, None
        if best.decision is Decision.ALLOW and not as_written:
            # The rule names the bare command; what runs is that name behind a wrapper, an
            # assignment or a path ("env LD_PRELOAD=… ls", "./ls"), which it did not allow.
            return Decision.PROMPT, _RENAMED
        return best.decision, best

    def add_rule(self, rule: PrefixRule) -> None:
        """Add a rule. Rule self-tests ran at construction; nothing further to validate."""
        self.rules.append(rule)
        if rule.origin.startswith("amendment"):
            self.amendments += 1

    # ------------------------------------------------------------- persistence
    def to_json(self) -> str:
        return json.dumps({"default": self.default.value,
                           "rules": [r.to_dict() for r in self.rules]}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "ExecPolicy":
        """Load a policy. Every rule's examples are validated here; a bad rule refuses load."""
        data = json.loads(text)
        rules = [PrefixRule.from_dict(r) for r in data.get("rules", [])]
        return cls(rules, default=Decision(data.get("default", "prompt")))

    @classmethod
    def load(cls, path: Path) -> "ExecPolicy":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    def stats(self) -> dict[str, Any]:
        by = {d.value: sum(1 for r in self.rules if r.decision is d) for d in Decision}
        return {"rules": len(self.rules), "by_decision": by, "evaluations": self.evaluations,
                "amendments": self.amendments, "default": self.default.value}
