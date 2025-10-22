#!/usr/bin/env node

import { readFile, readdir, mkdir, rm, writeFile } from 'fs/promises';
import { join, basename } from 'path';
import * as tar from 'tar';
import { encoding_for_model } from 'tiktoken';

/**
 * Extract a tar archive to a directory
 * @param {string} tarPath - Path to tar file
 * @param {string} outputDir - Directory to extract to
 */
async function extractTar(tarPath, outputDir) {
  await tar.extract({
    file: tarPath,
    cwd: outputDir
  });
}

/**
 * Get all HTML files in a directory
 * @param {string} dir - Directory to search
 * @returns {Promise<string[]>} - Array of HTML file paths
 */
async function getHtmlFiles(dir) {
  const files = await readdir(dir);
  return files
    .filter(file => file.endsWith('.html'))
    .map(file => join(dir, file));
}

/**
 * Count tokens in text using GPT tokenizer (cl100k_base encoding)
 * @param {string} text - Text to tokenize
 * @returns {number} - Number of tokens
 */
function countTokens(text) {
  const encoder = encoding_for_model('gpt-4');
  try {
    const tokens = encoder.encode(text);
    const count = tokens.length;
    encoder.free();
    return count;
  } catch (error) {
    encoder.free();
    throw error;
  }
}

/**
 * Get file size in bytes
 * @param {string} filePath - Path to file
 * @returns {Promise<number>} - File size in bytes
 */
async function getFileSize(filePath) {
  const content = await readFile(filePath, 'utf-8');
  return Buffer.byteLength(content, 'utf-8');
}

/**
 * Compare two HTML files
 * @param {string} originalPath - Path to original HTML file
 * @param {string} purgedPath - Path to purged HTML file
 * @param {string} filename - Base filename
 * @returns {Promise<Object>} - Comparison results
 */
async function compareFiles(originalPath, purgedPath, filename) {
  // Read file contents
  const originalContent = await readFile(originalPath, 'utf-8');
  const purgedContent = await readFile(purgedPath, 'utf-8');

  // Calculate sizes
  const originalSize = Buffer.byteLength(originalContent, 'utf-8');
  const purgedSize = Buffer.byteLength(purgedContent, 'utf-8');
  const sizeReduction = originalSize - purgedSize;
  const sizeReductionPercent = originalSize > 0
    ? ((sizeReduction / originalSize) * 100).toFixed(2)
    : 0;

  // Count tokens
  const originalTokens = countTokens(originalContent);
  const purgedTokens = countTokens(purgedContent);
  const tokenReduction = originalTokens - purgedTokens;
  const tokenReductionPercent = originalTokens > 0
    ? ((tokenReduction / originalTokens) * 100).toFixed(2)
    : 0;

  return {
    filename,
    size: {
      original: originalSize,
      purged: purgedSize,
      reduction: sizeReduction,
      reductionPercent: parseFloat(sizeReductionPercent)
    },
    tokens: {
      original: originalTokens,
      purged: purgedTokens,
      reduction: tokenReduction,
      reductionPercent: parseFloat(tokenReductionPercent)
    }
  };
}

/**
 * Calculate aggregate statistics from per-file comparisons
 * @param {Array} comparisons - Array of file comparison objects
 * @returns {Object} - Aggregate statistics
 */
function calculateAggregateStats(comparisons) {
  const totalFiles = comparisons.length;

  if (totalFiles === 0) {
    return {
      totalFiles: 0,
      size: {},
      tokens: {}
    };
  }

  // Size statistics
  const totalOriginalSize = comparisons.reduce((sum, c) => sum + c.size.original, 0);
  const totalPurgedSize = comparisons.reduce((sum, c) => sum + c.size.purged, 0);
  const totalSizeReduction = totalOriginalSize - totalPurgedSize;
  const avgSizeReduction = totalSizeReduction / totalFiles;
  const overallSizeReductionPercent = totalOriginalSize > 0
    ? ((totalSizeReduction / totalOriginalSize) * 100).toFixed(2)
    : 0;

  const sizeReductions = comparisons.map(c => c.size.reduction);
  const minSizeReduction = Math.min(...sizeReductions);
  const maxSizeReduction = Math.max(...sizeReductions);

  // Token statistics
  const totalOriginalTokens = comparisons.reduce((sum, c) => sum + c.tokens.original, 0);
  const totalPurgedTokens = comparisons.reduce((sum, c) => sum + c.tokens.purged, 0);
  const totalTokenReduction = totalOriginalTokens - totalPurgedTokens;
  const avgTokenReduction = totalTokenReduction / totalFiles;
  const overallTokenReductionPercent = totalOriginalTokens > 0
    ? ((totalTokenReduction / totalOriginalTokens) * 100).toFixed(2)
    : 0;

  const tokenReductions = comparisons.map(c => c.tokens.reduction);
  const minTokenReduction = Math.min(...tokenReductions);
  const maxTokenReduction = Math.max(...tokenReductions);

  return {
    totalFiles,
    size: {
      totalOriginal: totalOriginalSize,
      totalOriginalMB: (totalOriginalSize / 1024 / 1024).toFixed(2),
      totalPurged: totalPurgedSize,
      totalPurgedMB: (totalPurgedSize / 1024 / 1024).toFixed(2),
      totalReduction: totalSizeReduction,
      totalReductionMB: (totalSizeReduction / 1024 / 1024).toFixed(2),
      averageReduction: Math.round(avgSizeReduction),
      overallReductionPercent: parseFloat(overallSizeReductionPercent),
      minReduction: minSizeReduction,
      maxReduction: maxSizeReduction
    },
    tokens: {
      totalOriginal: totalOriginalTokens,
      totalPurged: totalPurgedTokens,
      totalReduction: totalTokenReduction,
      averageReduction: Math.round(avgTokenReduction),
      overallReductionPercent: parseFloat(overallTokenReductionPercent),
      minReduction: minTokenReduction,
      maxReduction: maxTokenReduction
    }
  };
}

/**
 * Compare two tar archives
 * @param {string} originalTarPath - Path to original tar file
 * @param {string} purgedTarPath - Path to purged tar file
 * @param {Object} options - Comparison options
 * @returns {Promise<Object>} - Comparison results
 */
async function compareTarArchives(originalTarPath, purgedTarPath, options = {}) {
  const {
    tempDir = '/tmp/compare-tar-temp',
    outputJsonPath = 'comparison-results.json'
  } = options;

  const originalExtractDir = join(tempDir, 'original');
  const purgedExtractDir = join(tempDir, 'purged');

  try {
    // Create temp directories
    console.log('Creating temporary directories...');
    await mkdir(originalExtractDir, { recursive: true });
    await mkdir(purgedExtractDir, { recursive: true });

    // Extract both tar files
    console.log(`Extracting ${basename(originalTarPath)}...`);
    await extractTar(originalTarPath, originalExtractDir);

    console.log(`Extracting ${basename(purgedTarPath)}...`);
    await extractTar(purgedTarPath, purgedExtractDir);

    // Get HTML files from both archives
    console.log('Finding HTML files...');
    const originalFiles = await getHtmlFiles(originalExtractDir);
    const purgedFiles = await getHtmlFiles(purgedExtractDir);

    console.log(`Found ${originalFiles.length} HTML files in original archive`);
    console.log(`Found ${purgedFiles.length} HTML files in purged archive`);

    // Create filename maps
    const originalFileMap = new Map();
    originalFiles.forEach(path => {
      const filename = basename(path);
      originalFileMap.set(filename, path);
    });

    const purgedFileMap = new Map();
    purgedFiles.forEach(path => {
      const filename = basename(path);
      purgedFileMap.set(filename, path);
    });

    // Find matching files
    const matchingFilenames = [...originalFileMap.keys()].filter(
      filename => purgedFileMap.has(filename)
    );

    console.log(`\nComparing ${matchingFilenames.length} matching HTML files...`);
    console.log('This may take a while (tokenization can be slow)...\n');

    // Compare each matching file
    const comparisons = [];
    let processedCount = 0;

    for (const filename of matchingFilenames) {
      const originalPath = originalFileMap.get(filename);
      const purgedPath = purgedFileMap.get(filename);

      try {
        const comparison = await compareFiles(originalPath, purgedPath, filename);
        comparisons.push(comparison);
        processedCount++;

        // Progress update every 100 files
        if (processedCount % 100 === 0) {
          console.log(`  Processed ${processedCount}/${matchingFilenames.length} files...`);
        }
      } catch (error) {
        console.error(`  Error comparing ${filename}: ${error.message}`);
      }
    }

    console.log(`\nCompleted comparison of ${comparisons.length} files\n`);

    // Calculate aggregate statistics
    console.log('Calculating aggregate statistics...');
    const summary = calculateAggregateStats(comparisons);

    // Prepare output
    const results = {
      metadata: {
        originalTar: originalTarPath,
        purgedTar: purgedTarPath,
        comparisonDate: new Date().toISOString(),
        tokenizerModel: 'gpt-4 (cl100k_base encoding)'
      },
      summary,
      perFile: comparisons.sort((a, b) =>
        b.size.reduction - a.size.reduction // Sort by size reduction descending
      )
    };

    // Write to JSON file
    console.log(`Writing results to ${outputJsonPath}...`);
    await writeFile(outputJsonPath, JSON.stringify(results, null, 2), 'utf-8');

    // Clean up temp directories
    console.log('Cleaning up temporary files...');
    await rm(tempDir, { recursive: true, force: true });

    return results;

  } catch (error) {
    // Clean up on error
    try {
      await rm(tempDir, { recursive: true, force: true });
    } catch (cleanupError) {
      // Ignore cleanup errors
    }

    throw error;
  }
}

/**
 * Display summary statistics
 * @param {Object} summary - Summary statistics object
 */
function displaySummary(summary) {
  console.log('\n' + '='.repeat(70));
  console.log('COMPARISON SUMMARY');
  console.log('='.repeat(70));
  console.log(`Total files compared: ${summary.totalFiles}`);
  console.log();
  console.log('FILE SIZE:');
  console.log(`  Original:  ${summary.size.totalOriginalMB} MB (${summary.size.totalOriginal.toLocaleString()} bytes)`);
  console.log(`  Purged:    ${summary.size.totalPurgedMB} MB (${summary.size.totalPurged.toLocaleString()} bytes)`);
  console.log(`  Reduction: ${summary.size.totalReductionMB} MB (${summary.size.totalReduction.toLocaleString()} bytes)`);
  console.log(`  Overall reduction: ${summary.size.overallReductionPercent}%`);
  console.log(`  Average per file: ${summary.size.averageReduction.toLocaleString()} bytes`);
  console.log(`  Range: ${summary.size.minReduction.toLocaleString()} - ${summary.size.maxReduction.toLocaleString()} bytes`);
  console.log();
  console.log('TOKEN COUNT:');
  console.log(`  Original:  ${summary.tokens.totalOriginal.toLocaleString()} tokens`);
  console.log(`  Purged:    ${summary.tokens.totalPurged.toLocaleString()} tokens`);
  console.log(`  Reduction: ${summary.tokens.totalReduction.toLocaleString()} tokens`);
  console.log(`  Overall reduction: ${summary.tokens.overallReductionPercent}%`);
  console.log(`  Average per file: ${summary.tokens.averageReduction.toLocaleString()} tokens`);
  console.log(`  Range: ${summary.tokens.minReduction.toLocaleString()} - ${summary.tokens.maxReduction.toLocaleString()} tokens`);
  console.log('='.repeat(70));
}

/**
 * Main function
 */
async function main() {
  const args = process.argv.slice(2);

  // Parse arguments
  let originalTar = null;
  let purgedTar = null;
  let outputJson = 'comparison-results.json';
  let tempDir = '/tmp/compare-tar-temp';

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];

    if (arg === '--output' || arg === '-o') {
      if (i + 1 < args.length) {
        outputJson = args[++i];
      }
    } else if (arg === '--temp' || arg === '-t') {
      if (i + 1 < args.length) {
        tempDir = args[++i];
      }
    } else if (arg === '--help' || arg === '-h') {
      console.log(`
Compare Tar Archives - HTML File Size and Token Count Comparison

Usage: node compare-tar-archives.js [options] <original-tar> <purged-tar>

Arguments:
  <original-tar>    Path to the original tar archive
  <purged-tar>      Path to the purged tar archive

Options:
  -o, --output <file>    Output JSON file path (default: comparison-results.json)
  -t, --temp <dir>       Temporary directory for extraction (default: /tmp/compare-tar-temp)
  -h, --help             Show this help message

Example:
  node compare-tar-archives.js \\
    /home/user/dataset/webdataset_chunk_00000.tar \\
    processed_dataset/webdataset_chunk_00000_purged.tar \\
    --output chunk_00000_comparison.json

Output:
  The script generates a JSON file with:
  - summary: Aggregate statistics (total files, size reduction, token reduction)
  - perFile: Detailed per-file comparison data

  Token counting uses GPT-4's cl100k_base tokenizer (tiktoken).
      `);
      process.exit(0);
    } else if (!originalTar) {
      originalTar = arg;
    } else if (!purgedTar) {
      purgedTar = arg;
    }
  }

  // Validate arguments
  if (!originalTar || !purgedTar) {
    console.error('Error: Both original and purged tar file paths are required');
    console.log('Use --help for usage information');
    process.exit(1);
  }

  console.log('Tar Archive Comparison Tool');
  console.log('============================\n');
  console.log(`Original tar: ${originalTar}`);
  console.log(`Purged tar:   ${purgedTar}`);
  console.log(`Output JSON:  ${outputJson}\n`);

  try {
    const results = await compareTarArchives(originalTar, purgedTar, {
      tempDir,
      outputJsonPath: outputJson
    });

    displaySummary(results.summary);
    console.log(`\nDetailed results saved to: ${outputJson}`);

  } catch (error) {
    console.error('\nFatal error:', error.message);
    console.error(error.stack);
    process.exit(1);
  }
}

// Run the script
main();
