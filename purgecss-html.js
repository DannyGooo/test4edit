#!/usr/bin/env node

import { readFile, writeFile } from 'fs/promises';
import { PurgeCSS } from 'purgecss';
import { load } from 'cheerio';
import { glob } from 'glob';
import { resolve } from 'path';
import posthtml from 'posthtml';
import htmlnano from 'htmlnano';
import postcss from 'postcss';
import selectorParser from 'postcss-selector-parser';

/**
 * Reserved keywords to avoid in minified names (ad-blockers, HTML/CSS reserved words)
 */
const RESERVED_NAMES = new Set(['ad', 'ads', 'banner', 'if', 'do', 'for']);

/**
 * Generate a short name from an index (a-z, then aa-zz, then aaa-zzz, etc.)
 * @param {number} index - The index to convert
 * @returns {string} - The short name
 */
function generateShortName(index) {
  let name = '';
  let num = index;

  // Determine length (1 char, 2 char, 3 char, etc.)
  let length = 1;
  let threshold = 26;

  while (num >= threshold) {
    num -= threshold;
    length++;
    threshold = Math.pow(26, length);
  }

  // Convert to base-26 letters
  for (let i = 0; i < length; i++) {
    name = String.fromCharCode(97 + (num % 26)) + name;
    num = Math.floor(num / 26);
  }

  // Skip reserved names
  if (RESERVED_NAMES.has(name)) {
    return generateShortName(index + 1);
  }

  return name;
}

/**
 * Extract all classes and IDs from HTML and CSS
 * @param {Object} $ - Cheerio instance
 * @param {string} css - CSS content
 * @returns {Object} - { classes: Set, ids: Set }
 */
function extractClassesAndIds($, css) {
  const classes = new Set();
  const ids = new Set();

  // Extract from HTML class attributes
  $('[class]').each((i, elem) => {
    const classAttr = $(elem).attr('class') || '';
    classAttr.split(/\s+/).forEach(cls => {
      if (cls.trim()) classes.add(cls.trim());
    });
  });

  // Extract from HTML id attributes
  $('[id]').each((i, elem) => {
    const idAttr = $(elem).attr('id');
    if (idAttr && idAttr.trim()) {
      ids.add(idAttr.trim());
    }
  });

  // Extract from CSS using regex (simple extraction for mapping)
  // Class selectors
  const classMatches = css.matchAll(/\.([a-zA-Z_][\w-]*)/g);
  for (const match of classMatches) {
    classes.add(match[1]);
  }

  // ID selectors
  const idMatches = css.matchAll(/#([a-zA-Z_][\w-]*)/g);
  for (const match of idMatches) {
    ids.add(match[1]);
  }

  return { classes, ids };
}

/**
 * Build mapping from original names to minified names
 * @param {Set} names - Set of original names
 * @param {Array} safelist - Names to exclude from minification
 * @returns {Map} - Map of original -> minified names
 */
function buildMinificationMapping(names, safelist = []) {
  const mapping = new Map();
  const safelistSet = new Set(safelist);

  // Filter out safelisted names and sort by length (longer names first for better compression)
  const namesToMinify = Array.from(names)
    .filter(name => !safelistSet.has(name))
    .sort((a, b) => b.length - a.length);

  // Generate short names
  namesToMinify.forEach((name, index) => {
    mapping.set(name, generateShortName(index));
  });

  return mapping;
}

/**
 * Minify CSS selectors using postcss
 * @param {string} css - CSS content
 * @param {Map} classMapping - Class name mappings
 * @param {Map} idMapping - ID name mappings
 * @returns {Promise<string>} - Transformed CSS
 */
async function minifyCssSelectors(css, classMapping, idMapping) {
  const result = await postcss([
    root => {
      root.walkRules(rule => {
        rule.selector = selectorParser(selectors => {
          selectors.walkClasses(classNode => {
            const originalClass = classNode.value;
            if (classMapping.has(originalClass)) {
              classNode.value = classMapping.get(originalClass);
            }
          });

          selectors.walkIds(idNode => {
            const originalId = idNode.value;
            if (idMapping.has(originalId)) {
              idNode.value = idMapping.get(originalId);
            }
          });

          // Handle attribute selectors like [class~="foo"]
          selectors.walkAttributes(attrNode => {
            if (attrNode.attribute === 'class' && attrNode.value) {
              const cleanValue = attrNode.value.replace(/['"]/g, '');
              if (classMapping.has(cleanValue)) {
                const mappedValue = classMapping.get(cleanValue);
                attrNode.setValue(mappedValue);
              }
            }
            if (attrNode.attribute === 'id' && attrNode.value) {
              const cleanValue = attrNode.value.replace(/['"]/g, '');
              if (idMapping.has(cleanValue)) {
                const mappedValue = idMapping.get(cleanValue);
                attrNode.setValue(mappedValue);
              }
            }
          });
        }).processSync(rule.selector);
      });
    }
  ]).process(css, { from: undefined });

  return result.css;
}

/**
 * Minify class and id attributes in HTML
 * @param {Object} $ - Cheerio instance
 * @param {Map} classMapping - Class name mappings
 * @param {Map} idMapping - ID name mappings
 */
function minifyHtmlAttributes($, classMapping, idMapping) {
  // Minify class attributes
  $('[class]').each((i, elem) => {
    const classAttr = $(elem).attr('class') || '';
    const classes = classAttr.split(/\s+/).filter(cls => cls.trim());

    const minifiedClasses = classes.map(cls => {
      return classMapping.has(cls) ? classMapping.get(cls) : cls;
    });

    if (minifiedClasses.length > 0) {
      $(elem).attr('class', minifiedClasses.join(' '));
    }
  });

  // Minify id attributes
  $('[id]').each((i, elem) => {
    const idAttr = $(elem).attr('id');
    if (idAttr && idMapping.has(idAttr)) {
      $(elem).attr('id', idMapping.get(idAttr));
    }
  });
}

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

    let purgedCss = purgeResult[0]?.css || '';
    const totalPurgedSize = purgedCss.length;

    // Calculate savings
    const bytesRemoved = totalOriginalSize - totalPurgedSize;
    const percentageReduction = totalOriginalSize > 0
      ? ((bytesRemoved / totalOriginalSize) * 100).toFixed(2)
      : 0;

    // === CLASS AND ID MINIFICATION ===
    // Extract all classes and IDs from HTML and CSS
    const { classes, ids } = extractClassesAndIds($, purgedCss);

    // Build minification mappings (respecting safelist)
    const classMapping = buildMinificationMapping(classes, safelist);
    const idMapping = buildMinificationMapping(ids, safelist);

    // Calculate original length of class/id names
    const originalClassLength = Array.from(classes).join('').length;
    const originalIdLength = Array.from(ids).join('').length;
    const originalNamesLength = originalClassLength + originalIdLength;

    // Minify CSS selectors
    purgedCss = await minifyCssSelectors(purgedCss, classMapping, idMapping);

    // Minify HTML class and id attributes
    minifyHtmlAttributes($, classMapping, idMapping);

    // Calculate minified length of class/id names
    const minifiedClassLength = Array.from(classMapping.values()).join('').length;
    const minifiedIdLength = Array.from(idMapping.values()).join('').length;
    const minifiedNamesLength = minifiedClassLength + minifiedIdLength;
    const nameBytesRemoved = originalNamesLength - minifiedNamesLength;

    // Remove all existing style tags
    styleTags.remove();

    // Ensure <body> element exists
    let body = $('body');
    if (body.length === 0) {
      // No body element exists, create one
      const html = $('html');
      if (html.length === 0) {
        // No html element either, wrap everything
        $('*').wrapAll('<html></html>');
      }
      // Wrap existing content in body if it exists
      const htmlContent = $('html').children();
      if (htmlContent.length > 0) {
        htmlContent.wrapAll('<body></body>');
      } else {
        $('html').append('<body></body>');
      }
      body = $('body');
    }

    // Add the purged CSS as a single style tag at the end of body
    body.append(`<style>${purgedCss}</style>`);

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
      classesMinified: classMapping.size,
      idsMinified: idMapping.size,
      nameBytesRemoved,
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
        if (result.classesMinified > 0 || result.idsMinified > 0) {
          console.log(`  Classes minified: ${result.classesMinified}, IDs minified: ${result.idsMinified}`);
          console.log(`  Name reduction: -${result.nameBytesRemoved} bytes`);
        }
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
