#!/bin/bash
# Rebuild index.html: template + diary fragments (newest first).
set -e
SITE=/var/www/agentchainlab
TMP=$(mktemp)
awk '/<!--DIARY-->/{exit} {print}' "$SITE/index.html.tpl" | sed '/<!--SUMMARY-->/{
s/<!--SUMMARY-->//; r '"$SITE"'/exec-summary.html
}' > "$TMP"
for f in $(ls -r "$SITE/diary/"*.html 2>/dev/null); do cat "$f" >> "$TMP"; done
awk '/<!--DIARY-->/{p=1;next} p{print}' "$SITE/index.html.tpl" >> "$TMP"
mv "$TMP" "$SITE/index.html"
chmod 644 "$SITE/index.html"
