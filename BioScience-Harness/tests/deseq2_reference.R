# R's DESeq2 on a count table, for the cross-checks in test_omics_deseq2_r.py.
#
#   Rscript deseq2_reference.R counts.tsv samples.tsv "~ condition" \
#       condition treated control 0.05 genes_out.tsv run_out.tsv
#
# counts.tsv: genes x samples, a header row of sample IDs, the gene ID first in each row.
# samples.tsv: one row per sample ID with its design variables, every one read as a
# factor. As in bioagent.omics.de_backends, the contrast factor's reference level is the
# denominator and any other factor's is its first level in C-locale order.
# DESeq() and results() run with their defaults except alpha, the FDR that independent
# filtering optimises. Writes the per-gene results and estimates, and the run's values:
# versions, size factors, the dispersion trend and the prior variance.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 9) {
  stop("usage: counts.tsv samples.tsv design factor numerator denominator alpha ",
       "genes_out.tsv run_out.tsv")
}
suppressPackageStartupMessages(library(DESeq2))
counts <- as.matrix(read.delim(args[1], row.names = 1, check.names = FALSE,
                               colClasses = "character"))
storage.mode(counts) <- "integer"
samples <- read.delim(args[2], row.names = 1, check.names = FALSE,
                      colClasses = "character")
samples <- samples[colnames(counts), , drop = FALSE]
factor_name <- args[4]
for (column in colnames(samples)) {
  levels <- sort(unique(samples[[column]]), method = "radix")
  if (column == factor_name) levels <- c(args[6], setdiff(levels, args[6]))
  samples[[column]] <- factor(samples[[column]], levels = levels)
}
alpha <- as.numeric(args[7])
dds <- DESeqDataSetFromMatrix(counts, samples, design = as.formula(args[3]))
dds <- DESeq(dds, quiet = TRUE)
res <- results(dds, contrast = c(factor_name, args[5], args[6]), alpha = alpha)
mc <- mcols(dds)
genes <- data.frame(gene = rownames(dds), baseMean = res$baseMean,
                    log2FoldChange = res$log2FoldChange, lfcSE = res$lfcSE,
                    stat = res$stat, pvalue = res$pvalue, padj = res$padj,
                    dispGeneEst = mc$dispGeneEst, dispFit = mc$dispFit,
                    dispMAP = mc$dispMAP, dispersion = mc$dispersion,
                    dispOutlier = mc$dispOutlier, allZero = mc$allZero)
write.table(genes, args[8], sep = "\t", quote = FALSE, row.names = FALSE, na = "NA")
fn <- dispersionFunction(dds)
coefs <- attr(fn, "coefficients")
model <- model.matrix(design(dds), colData(dds))
number <- function(x) sprintf("%.17g", unname(x))
run <- c(R = paste(R.version$major, R.version$minor, sep = "."),
         DESeq2 = as.character(packageVersion("DESeq2")),
         fitType = attr(fn, "fitType"),
         asymptDisp = number(coefs["asymptDisp"]), extraPois = number(coefs["extraPois"]),
         varLogDispEsts = number(attr(fn, "varLogDispEsts")),
         dispPriorVar = number(attr(fn, "dispPriorVar")),
         filterThreshold = number(metadata(res)$filterThreshold), alpha = number(alpha),
         residualDF = nrow(model) - ncol(model))
sf <- sizeFactors(dds)
run <- c(run, setNames(number(sf), paste0("sizeFactor:", names(sf))))
write.table(data.frame(key = names(run), value = run), args[9], sep = "\t",
            quote = FALSE, row.names = FALSE)
