#!/usr/bin/env bash
# Resolve a product Git tag under komawm to a dereferenced 40-hex commit SHA.
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: resolve-tag.sh <product> <tag>" >&2
    exit 1
fi

product="$1"
tag="$2"

ref_json="$(gh api "repos/komawm/${product}/git/ref/tags/${tag}")"
obj_type="$(jq -r '.object.type' <<<"$ref_json")"
obj_sha="$(jq -r '.object.sha' <<<"$ref_json")"

depth=0
while [[ "$obj_type" == "tag" ]]; do
    depth=$((depth + 1))
    if (( depth > 5 )); then
        echo "::error::Tag nesting too deep for ${product} ${tag}" >&2
        exit 1
    fi
    tag_json="$(gh api "repos/komawm/${product}/git/tags/${obj_sha}")"
    obj_type="$(jq -r '.object.type' <<<"$tag_json")"
    obj_sha="$(jq -r '.object.sha' <<<"$tag_json")"
done

if [[ "$obj_type" != "commit" || ! "$obj_sha" =~ ^[0-9a-f]{40}$ ]]; then
    echo "::error::Tag ${tag} does not resolve to a full commit (got ${obj_type} ${obj_sha})" >&2
    exit 1
fi

printf '%s\n' "$obj_sha"
