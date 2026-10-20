#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v xcodebuild >/dev/null
command -v xcodegen >/dev/null
xcodegen generate
xcodebuild -project EISAuditor.xcodeproj -scheme EISAuditor -configuration Release \
  -sdk iphoneos -destination 'generic/platform=iOS' -derivedDataPath build \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY='' build
mkdir -p build/ipa/Payload
cp -R build/Build/Products/Release-iphoneos/EISAuditor.app build/ipa/Payload/
mkdir -p ../../deliverables
(cd build/ipa && zip -qry ../../../../deliverables/EIS223-Auditor-iOS-unsigned-1.0.0.ipa Payload)
xcrun lipo -info build/Build/Products/Release-iphoneos/EISAuditor.app/EISAuditor
echo 'Unsigned device IPA built. Apple signing is required before installation.'
