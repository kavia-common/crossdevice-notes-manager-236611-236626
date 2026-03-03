#!/usr/bin/env bash
set -euo pipefail

# Smoke test for notes_backend API.
#
# Usage:
#   BASE_URL="http://localhost:3001" ./scripts/smoke_backend.sh
#
# For the hosted environment, you can set:
#   BASE_URL="https://<host>:3001" ./scripts/smoke_backend.sh

BASE_URL="${BASE_URL:-http://localhost:3001}"

EMAIL="smoke_$(date +%s)@example.com"
PW="password123"

echo "[1/9] Health check"
curl -fsS "$BASE_URL/" | sed -n '1,3p'
echo

echo "[2/9] Signup"
SIGNUP_JSON="$(curl -fsS -X POST "$BASE_URL/auth/signup" \
  -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}")"
TOKEN="$(python -c 'import sys, json; print(json.loads(sys.stdin.read())["access_token"])' <<<"$SIGNUP_JSON")"
echo "  token_len=${#TOKEN}"
echo

echo "[3/9] /auth/me"
curl -fsS -H "Authorization: Bearer $TOKEN" "$BASE_URL/auth/me"
echo; echo

echo "[4/9] Create a tag"
TAG_JSON="$(curl -fsS -X POST "$BASE_URL/tags" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"name":"work"}')"
TAG_ID="$(python -c 'import sys, json; print(json.loads(sys.stdin.read())["id"])' <<<"$TAG_JSON")"
echo "  tag_id=$TAG_ID"
echo

echo "[5/9] Create a note with tag_names"
NOTE_JSON="$(curl -fsS -X POST "$BASE_URL/notes" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"title":"Hello","content":"First content","tag_names":["work"]}')"
NOTE_ID="$(python -c 'import sys, json; print(json.loads(sys.stdin.read())["id"])' <<<"$NOTE_JSON")"
echo "  note_id=$NOTE_ID"
echo

echo "[6/9] Autosave update (PATCH)"
curl -fsS -X PATCH "$BASE_URL/notes/$NOTE_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"content":"Updated content","tag_names":["work","ideas"]}' | sed -n '1,3p'
echo; echo

echo "[7/9] Search list notes (q=Updated)"
curl -fsS "$BASE_URL/notes?q=Updated&limit=10&offset=0" \
  -H "Authorization: Bearer $TOKEN" | sed -n '1,5p'
echo; echo

echo "[8/9] List tags"
curl -fsS "$BASE_URL/tags" -H "Authorization: Bearer $TOKEN"
echo; echo

echo "[9/9] Delete note (then delete tag)"
curl -fsS -X DELETE "$BASE_URL/notes/$NOTE_ID" -H "Authorization: Bearer $TOKEN"
echo
curl -fsS -X DELETE "$BASE_URL/tags/$TAG_ID" -H "Authorization: Bearer $TOKEN"
echo
echo "Smoke test completed."
