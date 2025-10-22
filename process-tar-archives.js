#!/usr/bin/env node

import { readdir, mkdir, rm, stat } from 'fs/promises';
import { join, basename, dirname } from 'path';
import * as tar from 'tar';
import { existsSync } from 'fs';
import { processHtmlFile } from './purgecss-html.js';

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
 * Create a tar archive from a directory
 * @param {string} sourceDir - Directory to archive
 * @param {string} tarPath - Output tar file path
 */
async function createTar(sourceDir, tarPath) {
  const files = await readdir(sourceDir);
  await tar.create(
    {
      file: tarPath,
      cwd: sourceDir
    },
    files
  );
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
 * Process a single tar archive
 * @param {string} tarPath - Path to tar file
 * @param {Object} options - Processing options
 * @returns {Promise<Object>} - Processing results
 */
async function processTarArchive(tarPath, options = {}) {
  const {
    outputDir = null,
    tempDir = '/tmp/purgecss-temp',
    safelist = [],
    skipExisting = false
  } = options;

  const tarName = basename(tarPath, '.tar');
  const outputTarPath = outputDir
    ? join(outputDir, `${tarName}_purged.tar`)
    : join(dirname(tarPath), `${tarName}_purged.tar`);

  // Check if output already exists
  if (skipExisting && existsSync(outputTarPath)) {
    return {
      tarFile: tarPath,
      skipped: true,
      message: 'Output file already exists'
    };
  }

  const extractDir = join(tempDir, tarName);

  try {
    // Create temp directory
    await mkdir(extractDir, { recursive: true });

    // Extract tar file
    console.log(`  Extracting ${basename(tarPath)}...`);
    await extractTar(tarPath, extractDir);

    // Get all HTML files
    const htmlFiles = await getHtmlFiles(extractDir);

    if (htmlFiles.length === 0) {
      return {
        tarFile: tarPath,
        processed: false,
        message: 'No HTML files found in archive'
      };
    }

    console.log(`  Processing ${htmlFiles.length} HTML files...`);

    // Process each HTML file
    let totalOriginalSize = 0;
    let totalPurgedSize = 0;
    let filesProcessed = 0;
    let filesWithStyles = 0;

    for (const htmlFile of htmlFiles) {
      const result = await processHtmlFile(htmlFile, {
        dryRun: false,
        safelist,
        quiet: true
      });

      if (result.processed) {
        filesWithStyles++;
        totalOriginalSize += result.originalSize;
        totalPurgedSize += result.purgedSize;
        filesProcessed++;
      } else if (!result.error) {
        filesProcessed++;
      }
    }

    // Create new tar archive
    console.log(`  Creating purged archive...`);
    await createTar(extractDir, outputTarPath);

    // Clean up temp directory
    await rm(extractDir, { recursive: true, force: true });

    const bytesRemoved = totalOriginalSize - totalPurgedSize;
    const percentageReduction = totalOriginalSize > 0
      ? ((bytesRemoved / totalOriginalSize) * 100).toFixed(2)
      : 0;

    return {
      tarFile: tarPath,
      outputFile: outputTarPath,
      processed: true,
      totalFiles: htmlFiles.length,
      filesProcessed,
      filesWithStyles,
      originalSize: totalOriginalSize,
      purgedSize: totalPurgedSize,
      bytesRemoved,
      percentageReduction
    };

  } catch (error) {
    // Clean up on error
    try {
      await rm(extractDir, { recursive: true, force: true });
    } catch (cleanupError) {
      // Ignore cleanup errors
    }

    return {
      tarFile: tarPath,
      processed: false,
      error: error.message
    };
  }
}

/**
 * Main function to process all tar archives in a directory
 */
async function main() {
  const args = process.argv.slice(2);

  // Parse arguments
  let inputDir = null;
  let outputDir = null;
  let tempDir = '/tmp/purgecss-temp';
  let safelist = [];
  let skipExisting = false;
  let pattern = '*.tar';

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];

    if (arg === '--output' || arg === '-o') {
      if (i + 1 < args.length) {
        outputDir = args[++i];
      }
    } else if (arg === '--temp' || arg === '-t') {
      if (i + 1 < args.length) {
        tempDir = args[++i];
      }
    } else if (arg === '--safelist' || arg === '-s') {
      if (i + 1 < args.length) {
        safelist = args[++i].split(',').map(s => s.trim());
      }
    } else if (arg === '--skip-existing' || arg === '-k') {
      skipExisting = true;
    } else if (arg === '--pattern' || arg === '-p') {
      if (i + 1 < args.length) {
        pattern = args[++i];
      }
    } else if (arg === '--help' || arg === '-h') {
      console.log(`
PurgeCSS Tar Archive Processor

Usage: node process-tar-archives.js [options] <input-directory>

Options:
  -o, --output <dir>         Output directory for purged tar files (default: same as input)
  -t, --temp <dir>           Temporary directory for extraction (default: /tmp/purgecss-temp)
  -s, --safelist <classes>   Comma-separated list of CSS classes to preserve
  -k, --skip-existing        Skip processing if output file already exists
  -p, --pattern <pattern>    Tar file pattern to match (default: *.tar)
  -h, --help                 Show this help message

Examples:
  node process-tar-archives.js /path/to/tar/files
  node process-tar-archives.js --output ./output /path/to/tar/files
  node process-tar-archives.js --skip-existing /path/to/tar/files
  node process-tar-archives.js --safelist "active,modal" /path/to/tar/files
      `);
      process.exit(0);
    } else if (!inputDir) {
      inputDir = arg;
    }
  }

  if (!inputDir) {
    console.error('Error: No input directory provided');
    console.log('Use --help for usage information');
    process.exit(1);
  }

  // Check if input directory exists
  try {
    const stats = await stat(inputDir);
    if (!stats.isDirectory()) {
      console.error('Error: Input path is not a directory');
      process.exit(1);
    }
  } catch (error) {
    console.error(`Error: Cannot access input directory: ${error.message}`);
    process.exit(1);
  }

  // Create output directory if specified
  if (outputDir) {
    await mkdir(outputDir, { recursive: true });
  }

  // Find all tar files in input directory
  const allFiles = await readdir(inputDir);
  const tarFiles = allFiles
    .filter(file => file.match(pattern.replace('*', '.*')))
    .map(file => join(inputDir, file))
    .sort();

  if (tarFiles.length === 0) {
    console.error(`Error: No tar files matching pattern "${pattern}" found in ${inputDir}`);
    process.exit(1);
  }

  console.log(`Found ${tarFiles.length} tar file(s) to process\n`);
  console.log(`Output directory: ${outputDir || dirname(tarFiles[0])}`);
  console.log(`Temporary directory: ${tempDir}`);
  if (safelist.length > 0) {
    console.log(`Safelist: ${safelist.join(', ')}`);
  }
  if (skipExisting) {
    console.log(`Skip existing: enabled`);
  }
  console.log();

  // Process each tar file
  const results = [];
  let processedCount = 0;
  let skippedCount = 0;
  let errorCount = 0;

  for (let i = 0; i < tarFiles.length; i++) {
    const tarFile = tarFiles[i];
    console.log(`[${i + 1}/${tarFiles.length}] Processing ${basename(tarFile)}...`);

    const result = await processTarArchive(tarFile, {
      outputDir,
      tempDir,
      safelist,
      skipExisting
    });

    results.push(result);

    if (result.skipped) {
      console.log(`  Skipped (output already exists)\n`);
      skippedCount++;
    } else if (result.processed) {
      console.log(`  ✓ Complete`);
      console.log(`    Files with styles: ${result.filesWithStyles}/${result.totalFiles}`);
      console.log(`    CSS removed: ${result.bytesRemoved} bytes (${result.percentageReduction}%)`);
      console.log(`    Output: ${basename(result.outputFile)}\n`);
      processedCount++;
    } else if (result.error) {
      console.log(`  ✗ Error: ${result.error}\n`);
      errorCount++;
    } else {
      console.log(`  - ${result.message}\n`);
    }
  }

  // Summary
  console.log('='.repeat(60));
  console.log('SUMMARY');
  console.log('='.repeat(60));
  console.log(`Total tar files: ${tarFiles.length}`);
  console.log(`Processed: ${processedCount}`);
  console.log(`Skipped: ${skippedCount}`);
  console.log(`Errors: ${errorCount}`);

  const totalBytesRemoved = results
    .filter(r => r.processed)
    .reduce((sum, r) => sum + r.bytesRemoved, 0);

  const totalFilesWithStyles = results
    .filter(r => r.processed)
    .reduce((sum, r) => sum + r.filesWithStyles, 0);

  const totalHtmlFiles = results
    .filter(r => r.processed)
    .reduce((sum, r) => sum + r.totalFiles, 0);

  console.log(`Total HTML files: ${totalHtmlFiles}`);
  console.log(`Files with styles: ${totalFilesWithStyles}`);
  console.log(`Total CSS removed: ${(totalBytesRemoved / 1024 / 1024).toFixed(2)} MB`);

  // Clean up temp directory
  try {
    await rm(tempDir, { recursive: true, force: true });
  } catch (error) {
    // Ignore cleanup errors
  }

  console.log('\nDone!');
}

// Run the script
main().catch(error => {
  console.error('Fatal error:', error);
  process.exit(1);
});
