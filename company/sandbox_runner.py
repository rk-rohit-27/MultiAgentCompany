"""Trusted QA entrypoint; mounted readonly. Generated code runs only inside this container."""

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

issues = []


def issue(owner, text):
    issues.append({"owner": owner, "severity": "high", "description": text[:2000]})


def run(owner, args, cwd, timeout=75):
    # Output redirected to bounded filesystem so communicate cannot consume unbounded host RAM.
    log = Path("/tmp") / f"{owner}-{len(issues)}.log"
    with log.open("w+") as output:
        try:
            result = subprocess.run(
                args,
                cwd=cwd,
                stdout=output,
                stderr=output,
                timeout=timeout,
                env={**os.environ, "HOME": "/tmp", "APP_DATA_DIR": "/tmp/app-data"},
            )
        except subprocess.TimeoutExpired:
            issue(owner, f"Timed out: {args[:2]}")
            return False
        if result.returncode:
            output.seek(0)
            issue(owner, f"Failed {args[:2]}: {output.read(1800)}")
            return False
    return True


shutil.copytree("/input", "/tmp/project")
root = Path("/tmp/project")
backend = root / "backend"
frontend = root / "frontend"
contract = json.loads((root / "contract.json").read_text())
for source in backend.rglob("*.py"):
    try:
        ast.parse(source.read_text())
    except SyntaxError as e:
        issue("backend", f"{source.relative_to(backend)}: syntax error {e}")
run("backend", ["ruff", "check", "--isolated", "--select", "E9,F63,F7,F82", "."], backend)
run("backend", ["python", "-m", "pytest", "-q", "--import-mode=prepend", "tests"], backend)
# Import/OpenAPI extraction is a separate process; stdout does not go to outer result channel.
extract = (
    "import json; from main import app; "
    "open('/tmp/openapi.json','w').write(json.dumps(app.openapi()))"
)
if run("backend", ["python", "-c", extract], backend):
    spec = json.loads(Path("/tmp/openapi.json").read_text())
    schemas = spec.get("components", {}).get("schemas", {})

    def resolve(schema):
        if "$ref" in schema:
            key = schema["$ref"].split("/")[-1]
            return resolve(schemas[key])
        return schema

    def compatible(expected, actual, seen=None):
        """Check approved structural constraints against actual OpenAPI, ignoring titles."""
        seen = set() if seen is None else set(seen)
        ref = actual.get("$ref")
        if ref:
            if ref in seen:
                return True  # finite comparison of recursive model
            seen.add(ref)
        actual = resolve(actual)
        for key in (
            "type",
            "enum",
            "format",
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
            "pattern",
            "additionalProperties",
            "minItems",
            "maxItems",
        ):
            if key in expected and expected[key] != actual.get(key):
                return False
        if set(expected.get("required", [])) != set(actual.get("required", [])):
            return False
        for key, value in expected.get("properties", {}).items():
            if key not in actual.get("properties", {}) or not compatible(
                value, actual["properties"][key], seen
            ):
                return False
        if "items" in expected and not compatible(expected["items"], actual.get("items", {}), seen):
            return False
        for key in ("anyOf", "oneOf", "allOf"):
            if key in expected:
                if len(expected[key]) != len(actual.get(key, [])):
                    return False
                if not all(compatible(e, a, seen) for e, a in zip(expected[key], actual[key])):
                    return False
        return True

    for endpoint in contract["endpoints"]:
        path, method = endpoint["path"], endpoint["method"].lower()
        operation = spec.get("paths", {}).get(path, {}).get(method)
        if not operation:
            issue("backend", f"Missing API operation: {method.upper()} {path}")
            continue
        response = operation.get("responses", {}).get(str(endpoint["success_status"]), {})
        actual = response.get("content", {}).get("application/json", {}).get("schema", {})
        if not compatible(endpoint["response_schema"], actual):
            issue("backend", f"Response schema/status mismatch: {method.upper()} {path}")
        expected = endpoint.get("request_schema")
        if expected is not None:
            actual = (
                operation.get("requestBody", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            if not compatible(expected, actual):
                issue("backend", f"Request schema mismatch: {method.upper()} {path}")

if not (frontend / "app/layout.tsx").is_file() or not (frontend / "app/page.tsx").is_file():
    issue("frontend", "Required Next.js entrypoints missing")
else:
    # Never invoke generated package.json scripts or install generated dependency lists.
    (frontend / "node_modules").symlink_to("/opt/js/node_modules", target_is_directory=True)
    config = {
        "compilerOptions": {
            "target": "ES2020",
            "lib": ["dom", "esnext"],
            "strict": True,
            "skipLibCheck": True,
            "noEmit": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "jsx": "preserve",
            "esModuleInterop": True,
            "resolveJsonModule": True,
            "isolatedModules": True,
            "baseUrl": ".",
            "paths": {"@/*": ["./*"]},
        },
        "include": ["**/*.ts", "**/*.tsx"],
        "exclude": ["node_modules"],
    }
    (frontend / "tsconfig.json").write_text(json.dumps(config))
    run("frontend", ["node", "/opt/js/node_modules/typescript/bin/tsc", "--noEmit"], frontend)
    package = json.loads((frontend / "package.json").read_text())
    allowed = {
        "next",
        "react",
        "react-dom",
        "typescript",
        "@types/react",
        "@types/react-dom",
        "@types/node",
    }
    dependencies = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
    if dependencies - allowed:
        issue(
            "frontend", "Dependencies unavailable in pinned sandbox: " + str(dependencies - allowed)
        )

print(json.dumps({"issues": issues}), flush=True)
sys.exit(0)
