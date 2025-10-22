#!/usr/bin/env node

import { readFile, writeFile } from 'fs/promises';
import { PurgeCSS } from 'purgecss';
import { load } from 'cheerio';
import { glob } from 'glob';
import { resolve } from 'path';
import posthtml from 'posthtml';
import htmlnano from 'htmlnano';

/**
 * Process a single HTML file through PurgeCSS
 * @param {string} filePath - Path to the HTML file
 * @param {Object} options - Processing options
 * @returns {Promise<Object>} - Processing results
 */
export async function processHtmlFile(filePath, options = {}) {
  const { dryRun = false, safelist = [], quiet = false, minify = true } = options;

  try {
    // Read the HTML file
    const html = await readFile(filePath, 'utf-8');
    const $ = load(html);

    // Find all style tags
    const styleTags = $('style');

    if (styleTags.length === 0) {
      return {
        file: filePath,
        processed: false,
        message: 'No <style> tags found'
      };
    }

    // Collect all CSS from all style tags
    const allCss = [];
    let totalOriginalSize = 0;

    styleTags.each((i, elem) => {
      const css = $(elem).html() || '';
      if (css.trim()) {
        allCss.push(css);
        totalOriginalSize += css.length;
      }
    });

    if (allCss.length === 0) {
      return {
        file: filePath,
        processed: false,
        message: 'No CSS content found in <style> tags'
      };
    }

    // Merge all CSS into a single string
    const mergedCss = allCss.join('\n\n');

    // Run PurgeCSS on the merged CSS
    const purgeResult = await new PurgeCSS().purge({
      content: [
        {
          raw: html,
          extension: 'html'
        }
      ],
      css: [
        {
          raw: mergedCss
        }
      ],
      safelist: {
        standard: safelist,
        deep: [],
        greedy: [],
        keyframes: [],
        variables: []
      }
    });

    const purgedCss = purgeResult[0]?.css || '';
    const totalPurgedSize = purgedCss.length;

    // Calculate savings
    const bytesRemoved = totalOriginalSize - totalPurgedSize;
    const percentageReduction = totalOriginalSize > 0
      ? ((bytesRemoved / totalOriginalSize) * 100).toFixed(2)
      : 0;

    // Remove all existing style tags
    styleTags.remove();

    // Ensure <head> element exists
    let head = $('head');
    if (head.length === 0) {
      // No head element exists, create one
      const html = $('html');
      if (html.length === 0) {
        // No html element either, wrap everything
        $('*').wrapAll('<html></html>');
      }
      $('html').prepend('<head></head>');
      head = $('head');
    }

    // Add the purged CSS as a single style tag in head
    head.append(`<style>${purgedCss}</style>`);

    // Get the processed HTML
    let outputHtml = $.html();
    const htmlSizeBeforeMinify = outputHtml.length;
    let htmlBytesRemoved = 0;

    // Minify HTML if enabled
    if (minify) {
      const result = await posthtml([
        htmlnano({
          collapseWhitespace: 'all',
          removeComments: 'all',
          removeEmptyAttributes: true,
          removeRedundantAttributes: true,
          collapseBooleanAttributes: true,
          minifyJs: false,
          minifyCss: true, // Minify CSS in style tags (remove comments, whitespace)
          minifySvg: false // Skip SVG to avoid needing svgo
        })
      ]).process(outputHtml);
      outputHtml = result.html;
      htmlBytesRemoved = htmlSizeBeforeMinify - outputHtml.length;
    }

    // Write back to file if not dry run
    if (!dryRun) {
      await writeFile(filePath, outputHtml, 'utf-8');
    }

    return {
      file: filePath,
      processed: true,
      stylesProcessed: allCss.length,
      stylesMerged: true,
      originalSize: totalOriginalSize,
      purgedSize: totalPurgedSize,
      bytesRemoved,
      percentageReduction,
      minified: minify,
      htmlBytesRemoved,
      dryRun
    };

  } catch (error) {
    return {
      file: filePath,
      processed: false,
      error: error.message
    };
  }
}

/**
 * Main function to process HTML files
 */
async function main() {
  const args = process.argv.slice(2);

  // Parse arguments
  let patterns = [];
  let safelist = [];
  let dryRun = false;
  let quiet = false;
  let minify = true;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];

    if (arg === '--dry-run' || arg === '-d') {
      dryRun = true;
    } else if (arg === '--quiet' || arg === '-q') {
      quiet = true;
    } else if (arg === '--no-minify') {
      minify = false;
    } else if (arg === '--safelist' || arg === '-s') {
      // Next argument should be comma-separated safelist
      if (i + 1 < args.length) {
        safelist = args[++i].split(',').map(s => s.trim());
      }
    } else if (arg === '--help' || arg === '-h') {
      console.log(`
PurgeCSS HTML Transformer

Usage: node purgecss-html.js [options] <file-or-pattern>...

Options:
  -d, --dry-run              Preview changes without modifying files
  -q, --quiet                Minimal output (only summary)
  --no-minify                Disable HTML minification (keep formatting)
  -s, --safelist <classes>   Comma-separated list of CSS classes to preserve
  -h, --help                 Show this help message

Examples:
  node purgecss-html.js index.html
  node purgecss-html.js "**/*.html"
  node purgecss-html.js --dry-run index.html
  node purgecss-html.js --no-minify index.html
  node purgecss-html.js --safelist "active,selected" index.html
      `);
      process.exit(0);
    } else {
      patterns.push(arg);
    }
  }

  if (patterns.length === 0) {
    console.error('Error: No file patterns provided');
    console.log('Use --help for usage information');
    process.exit(1);
  }

  // Resolve file patterns
  let files = [];
  for (const pattern of patterns) {
    const matches = await glob(pattern, {
      absolute: true,
      nodir: true
    });
    files.push(...matches);
  }

  // Remove duplicates
  files = [...new Set(files)];

  if (files.length === 0) {
    console.error('Error: No files matched the provided patterns');
    process.exit(1);
  }

  console.log(`Processing ${files.length} file(s)${dryRun ? ' (DRY RUN)' : ''}...\n`);

  // Process each file
  const results = [];
  for (const file of files) {
    const result = await processHtmlFile(file, { dryRun, safelist, quiet, minify });
    results.push(result);
  }

  // Display results
  if (!quiet) {
    console.log('Results:');
    console.log('========\n');
  }

  let totalBytesRemoved = 0;
  let totalFilesProcessed = 0;

  for (const result of results) {
    if (result.processed) {
      totalFilesProcessed++;
      totalBytesRemoved += result.bytesRemoved;

      if (!quiet) {
        console.log(`✓ ${result.file}`);
        console.log(`  Styles processed: ${result.stylesProcessed}`);
        console.log(`  CSS: ${result.originalSize} → ${result.purgedSize} bytes (-${result.bytesRemoved} bytes, ${result.percentageReduction}%)`);
        if (result.minified && result.htmlBytesRemoved > 0) {
          console.log(`  HTML minified: -${result.htmlBytesRemoved} bytes`);
        }
        if (result.dryRun) {
          console.log(`  (Not modified - dry run)`);
        }
        console.log();
      }
    } else if (result.error) {
      if (!quiet) {
        console.log(`✗ ${result.file}`);
        console.log(`  Error: ${result.error}`);
        console.log();
      }
    } else {
      if (!quiet) {
        console.log(`- ${result.file}`);
        console.log(`  ${result.message}`);
        console.log();
      }
    }
  }

  // Summary
  console.log('Summary:');
  console.log('========');
  console.log(`Files processed: ${totalFilesProcessed}/${files.length}`);
  console.log(`Total CSS removed: ${totalBytesRemoved} bytes`);

  if (dryRun) {
    console.log('\nThis was a dry run. No files were modified.');
    console.log('Run without --dry-run to apply changes.');
  }
}

// Run the script only if this file is executed directly
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(error => {
    console.error('Fatal error:', error);
    process.exit(1);
  });
}
