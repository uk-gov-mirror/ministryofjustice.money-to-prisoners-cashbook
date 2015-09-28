/* jshint node: true */

'use strict';

var mainBowerFiles = require('main-bower-files');
var vendorFiles = mainBowerFiles();
var appBase = 'mtp_cashbook/';
var baseAssetsPath = appBase + 'assets-src/';

var paths = {
  src: baseAssetsPath,
  dest: appBase + 'assets/',
  styles: [
    baseAssetsPath + 'stylesheets/**/*.scss'
  ],
  scripts: [
    baseAssetsPath + 'javascripts/**/*.js'
  ],
  vendorScripts: vendorFiles.concat([
    baseAssetsPath + 'bower_components/checked-polyfill/checked-polyfill.js'
  ]),
  images: [
    baseAssetsPath + 'images/**/*',
    baseAssetsPath + 'bower_components/hopscotch-highlight/src/images/**/*'
  ],
  templates: [
    appBase + 'templates/**/*'
  ]
};

module.exports = paths;
