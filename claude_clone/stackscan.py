"""Scanner de stack DÉTERMINISTE (aucun LLM) : ce que le skill « stack-map » donne au superviseur comme matière première.

Principe : chaque fait vient d'un fichier précis (champ `evidence`), jamais d'une supposition. Lecture seule, bornée
(nombre de fichiers, taille, temps), confinée au dossier de travail. Les fichiers `.env*` ne sont JAMAIS lus (seule leur
présence est signalée ; les noms de variables viennent de `.env.example` et du code). Les valeurs de secrets ne sont jamais
renvoyées : les détections heuristiques ne donnent que le fichier et le type.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import tomllib
from collections import Counter

import yaml
from langchain_core.tools import ToolException, tool

from claude_clone import tools as T
from claude_clone import web

MAX_FILES, MAX_DEPTH, TIME_BUDGET, MAX_READ = 20_000, 8, 12.0, 200_000
EXTRA_SKIP = {"target", ".next", ".tox", "vendor", ".gradle", ".idea", ".vscode", "coverage", "out", ".mypy_cache", ".ruff_cache", ".terraform"}

LANG = {".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
        ".java": "Java", ".kt": "Kotlin", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".cs": "C#", ".c": "C", ".h": "C/C++",
        ".cpp": "C++", ".sql": "SQL", ".sh": "Shell", ".vue": "Vue", ".svelte": "Svelte", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
        ".tf": "Terraform", ".swift": "Swift", ".scala": "Scala", ".dart": "Dart", ".lua": "Lua", ".ipynb": "Jupyter"}
MANIFESTS = {"pyproject.toml": "python", "requirements.txt": "python", "Pipfile": "python", "setup.py": "python", "setup.cfg": "python",
             "package.json": "node", "pom.xml": "java", "build.gradle": "java", "build.gradle.kts": "java", "go.mod": "go",
             "Cargo.toml": "rust", "Gemfile": "ruby", "composer.json": "php"}
LOCKFILES = {"poetry.lock", "uv.lock", "Pipfile.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "go.sum",
             "Cargo.lock", "Gemfile.lock", "composer.lock", "gradle.lockfile"}

# nom normalisé → (catégorie, libellé)
CATALOG = {
    "fastapi": ("web", "FastAPI"), "django": ("web", "Django"), "flask": ("web", "Flask"), "starlette": ("web", "Starlette"), "aiohttp": ("web", "aiohttp"),
    "uvicorn": ("serveur", "Uvicorn"), "gunicorn": ("serveur", "Gunicorn"), "pydantic": ("validation", "Pydantic"), "sqlalchemy": ("orm", "SQLAlchemy"),
    "alembic": ("migrations", "Alembic"), "psycopg": ("db-driver", "PostgreSQL"), "psycopg2": ("db-driver", "PostgreSQL"), "psycopg2-binary": ("db-driver", "PostgreSQL"),
    "asyncpg": ("db-driver", "PostgreSQL"), "pymysql": ("db-driver", "MySQL"), "pymongo": ("db-driver", "MongoDB"), "redis": ("cache/queue", "Redis"),
    "celery": ("queue", "Celery"), "pytest": ("test", "pytest"), "pytest-asyncio": ("test", "pytest-asyncio"), "ruff": ("lint", "Ruff"), "black": ("lint", "Black"),
    "flake8": ("lint", "flake8"), "mypy": ("typage", "mypy"), "pyright": ("typage", "pyright"), "langchain": ("llm", "LangChain"), "langgraph": ("llm", "LangGraph"),
    "langchain-openai": ("llm", "LangChain OpenAI"), "openai": ("llm", "OpenAI SDK"), "anthropic": ("llm", "Anthropic SDK"), "llama-index": ("llm", "LlamaIndex"),
    "transformers": ("llm/ml", "Transformers"), "torch": ("ml", "PyTorch"), "numpy": ("data", "NumPy"), "pandas": ("data", "pandas"), "httpx": ("http", "httpx"),
    "requests": ("http", "requests"), "authlib": ("auth", "Authlib"), "pyjwt": ("auth", "PyJWT"), "python-jose": ("auth", "python-jose"), "python-keycloak": ("auth", "Keycloak"),
    "mcp": ("protocole", "MCP"), "playwright": ("e2e", "Playwright"), "rich": ("cli", "Rich"), "typer": ("cli", "Typer"), "click": ("cli", "Click"),
    "react": ("frontend", "React"), "next": ("frontend", "Next.js"), "vue": ("frontend", "Vue"), "nuxt": ("frontend", "Nuxt"), "svelte": ("frontend", "Svelte"),
    "@angular/core": ("frontend", "Angular"), "express": ("web", "Express"), "fastify": ("web", "Fastify"), "@nestjs/core": ("web", "NestJS"), "vite": ("build", "Vite"),
    "webpack": ("build", "webpack"), "typescript": ("typage", "TypeScript"), "jest": ("test", "Jest"), "vitest": ("test", "Vitest"), "cypress": ("e2e", "Cypress"),
    "eslint": ("lint", "ESLint"), "prettier": ("lint", "Prettier"), "prisma": ("orm", "Prisma"), "typeorm": ("orm", "TypeORM"), "mongoose": ("orm", "Mongoose"),
    "tailwindcss": ("css", "Tailwind"), "axios": ("http", "axios"), "zod": ("validation", "Zod"), "redux": ("état", "Redux"),
    "spring-boot": ("web", "Spring Boot"), "spring-boot-starter-web": ("web", "Spring Boot"), "hibernate-core": ("orm", "Hibernate"), "junit": ("test", "JUnit"),
    "lombok": ("java", "Lombok"), "gin": ("web", "Gin"), "gorm": ("orm", "GORM"), "rails": ("web", "Rails"), "laravel/framework": ("web", "Laravel"),
}
IMAGE_DB = {"postgres": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "mongo": "MongoDB", "redis": "Redis", "elasticsearch": "Elasticsearch",
            "opensearch": "OpenSearch", "rabbitmq": "RabbitMQ", "kafka": "Kafka", "minio": "MinIO", "keycloak": "Keycloak", "nginx": "nginx", "traefik": "Traefik",
            "clickhouse": "ClickHouse", "qdrant": "Qdrant", "pgvector": "pgvector", "grafana": "Grafana", "prometheus": "Prometheus"}
# Fin de support (table INTERNE à vérifier : les dates ne sont pas relues en ligne)
EOL = {"python": {"3.8": "2024-10", "3.9": "2025-10", "3.10": "2026-10", "3.11": "2027-10", "3.12": "2028-10", "3.13": "2029-10"},
       "node": {"16": "2023-09", "18": "2025-04", "20": "2026-04", "22": "2027-04", "24": "2028-04"}}
SECRET_PATTERNS = {"clé de type sk-…": re.compile(r"\bsk-[A-Za-z0-9_\-]{24,}"), "clé AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
                   "clé privée PEM": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "jeton GitHub": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
                   "jeton Slack": re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{20,}")}
ENV_CODE = re.compile(r"""(?:os\.environ\[\s*|os\.environ\.get\(\s*|os\.getenv\(\s*|process\.env\.|import\.meta\.env\.|getenv\(\s*)["']?([A-Z][A-Z0-9_]{2,})""")


SYSTEM_ENV = {"HOME", "PATH", "USER", "USERNAME", "SHELL", "PWD", "LANG", "LC_ALL", "TERM", "TMPDIR", "TEMP", "TMP", "HOSTNAME", "LOGNAME",
              "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "CI", "EDITOR", "DISPLAY", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"}
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "fixtures", "e2e", "testdata", "mocks"}


def _is_test_path(rel: str) -> bool:
    """Fichiers de test : leurs chaînes (fausses variables, fausses clés) ne décrivent pas la configuration du projet."""
    parts = rel.replace("\\", "/").split("/")
    base = parts[-1]
    return bool(TEST_DIRS & set(parts[:-1])) or bool(re.match(r"^(test_.*|.*_test\.\w+|.*\.(test|spec)\.\w+|conftest\.py)$", base))


def _norm(name: str) -> str:
    return re.sub(r"[_.]+", "-", name.strip().lower())


def _read(path: str, limit: int = MAX_READ) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _yaml(text: str):
    """safe_load avec garde contre les « bombes » d'alias (un dépôt hostile pourrait en fournir)."""
    if len(text) > 200_000 or text.count("*") > 200 or text.count("&") > 200:
        return None
    try:
        return yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError, MemoryError):
        return None


# --------------------------------------------------------------------------- #
# Parseurs de manifests
# --------------------------------------------------------------------------- #
def _pep508(req: str) -> tuple[str, str]:
    m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*(\[[^\]]*\])?\s*(.*?)\s*(;.*)?$", req)
    return (m.group(1), m.group(3).strip()) if m else (req.strip(), "")


def parse_python(rel: str, path: str) -> dict:
    name = os.path.basename(path)
    runtime, dev, extra = [], [], {}
    if name == "pyproject.toml":
        try:
            data = tomllib.loads(_read(path))
        except tomllib.TOMLDecodeError:
            return {"error": "pyproject.toml illisible"}
        proj = data.get("project", {})
        runtime += [dict(zip(("name", "spec"), _pep508(r))) for r in proj.get("dependencies", [])]
        for group, reqs in proj.get("optional-dependencies", {}).items():
            dev += [{**dict(zip(("name", "spec"), _pep508(r))), "group": group} for r in reqs]
        for group, reqs in data.get("dependency-groups", {}).items():
            dev += [{**dict(zip(("name", "spec"), _pep508(r))), "group": group} for r in reqs if isinstance(r, str)]
        poetry = data.get("tool", {}).get("poetry", {})
        for n, spec in poetry.get("dependencies", {}).items():
            if n.lower() == "python":
                extra["requires_python"] = spec if isinstance(spec, str) else str(spec)
            else:
                runtime.append({"name": n, "spec": spec if isinstance(spec, str) else str(spec.get("version", "*")) if isinstance(spec, dict) else "*"})
        for g in poetry.get("group", {}).values():
            for n, spec in g.get("dependencies", {}).items():
                dev.append({"name": n, "spec": spec if isinstance(spec, str) else "*", "group": "poetry-group"})
        if proj.get("requires-python"):
            extra["requires_python"] = proj["requires-python"]
        extra["build_backend"] = data.get("build-system", {}).get("build-backend")
        extra["scripts"] = sorted({**proj.get("scripts", {}), **poetry.get("scripts", {})})[:15]
        extra["package"] = proj.get("name") or poetry.get("name")
        tools_cfg = sorted(k for k in data.get("tool", {}) if k in ("ruff", "black", "mypy", "pytest", "coverage", "isort", "pyright", "uv", "poetry", "hatch", "setuptools"))
        extra["tool_sections"] = tools_cfg
        mypy = data.get("tool", {}).get("mypy", {})
        if mypy:
            extra["mypy_strict"] = bool(mypy.get("strict"))
    elif name.startswith("requirements") and name.endswith(".txt"):
        for line in _read(path).splitlines():
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith(("-", "git+", "http")):
                continue
            n, spec = _pep508(line)
            (dev if re.search(r"dev|test", name) else runtime).append({"name": n, "spec": spec})
    return {"runtime": runtime, "dev": dev, **extra}


def parse_node(rel: str, path: str) -> dict:
    try:
        data = json.loads(_read(path))
    except (ValueError, RecursionError):
        return {"error": "package.json illisible"}
    if not isinstance(data, dict):
        return {"error": "package.json inattendu"}
    return {"runtime": [{"name": k, "spec": str(v)} for k, v in (data.get("dependencies") or {}).items()],
            "dev": [{"name": k, "spec": str(v), "group": "dev"} for k, v in (data.get("devDependencies") or {}).items()],
            "requires_node": (data.get("engines") or {}).get("node"), "package_manager": data.get("packageManager"),
            "scripts": sorted((data.get("scripts") or {}))[:20], "package": data.get("name"), "workspaces": bool(data.get("workspaces")),
            "type": data.get("type"), "main": data.get("main")}


def parse_java(rel: str, path: str) -> dict:
    text = _read(path)
    runtime = []
    if path.endswith("pom.xml"):
        for g, a, v in re.findall(r"<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>(?:\s*<version>([^<]*)</version>)?", text):
            runtime.append({"name": f"{g.strip()}:{a.strip()}", "spec": (v or "").strip()})
        jv = re.search(r"<(?:java\.version|maven\.compiler\.(?:source|release)|release)>([^<]+)<", text)
        parent = re.search(r"<parent>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>\s*<version>([^<]+)</version>", text)
        return {"runtime": runtime, "dev": [], "java_version": jv.group(1) if jv else None, "parent": ":".join(parent.groups()) if parent else None}
    for kind, g, a, v in re.findall(r"""\b(implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(?\s*['"]([^'":]+):([^'":]+):?([^'")]*)['"]""", text):
        runtime.append({"name": f"{g}:{a}", "spec": v, "group": kind})
    return {"runtime": [d for d in runtime if d.get("group") != "testImplementation"], "dev": [d for d in runtime if d.get("group") == "testImplementation"]}


def parse_go(rel: str, path: str) -> dict:
    text = _read(path)
    mod = re.search(r"^module\s+(\S+)", text, re.M)
    gov = re.search(r"^go\s+(\S+)", text, re.M)
    reqs = re.findall(r"^\s*(?:require\s+)?([\w.\-/]+\.[\w.\-/]+)\s+(v[\w.\-+]+)", text, re.M)
    return {"runtime": [{"name": n, "spec": v} for n, v in reqs], "dev": [], "package": mod.group(1) if mod else None, "go_version": gov.group(1) if gov else None}


def parse_cargo(rel: str, path: str) -> dict:
    try:
        data = tomllib.loads(_read(path))
    except tomllib.TOMLDecodeError:
        return {"error": "Cargo.toml illisible"}
    norm = lambda d: [{"name": k, "spec": v if isinstance(v, str) else str(v.get("version", "*")) if isinstance(v, dict) else "*"} for k, v in d.items()]
    return {"runtime": norm(data.get("dependencies", {})), "dev": norm(data.get("dev-dependencies", {})), "package": data.get("package", {}).get("name"),
            "edition": data.get("package", {}).get("edition")}


def parse_simple(rel: str, path: str) -> dict:
    text, base = _read(path), os.path.basename(path)
    if base == "Gemfile":
        return {"runtime": [{"name": n, "spec": s or ""} for n, s in re.findall(r"^\s*gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", text, re.M)], "dev": []}
    try:
        data = json.loads(text)  # composer.json
    except ValueError:
        return {"error": f"{base} illisible"}
    req = data.get("require", {}) if isinstance(data, dict) else {}
    return {"runtime": [{"name": k, "spec": str(v)} for k, v in req.items() if k != "php"], "dev": [], "requires_php": req.get("php")}


PARSERS = {"python": parse_python, "node": parse_node, "java": parse_java, "go": parse_go, "rust": parse_cargo, "ruby": parse_simple, "php": parse_simple}
PARSE_BY_FILE = {"Cargo.toml": parse_cargo, "go.mod": parse_go, "pom.xml": parse_java, "build.gradle": parse_java, "build.gradle.kts": parse_java,
                 "Gemfile": parse_simple, "composer.json": parse_simple, "package.json": parse_node}


# --------------------------------------------------------------------------- #
# Infra / CI / données
# --------------------------------------------------------------------------- #
def parse_dockerfile(rel: str, path: str) -> dict:
    text = _read(path)
    images = re.findall(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)", text, re.M | re.I)
    flags = []
    for img in images:
        m = re.match(r"(?:.*/)?(python|node):(\d+(?:\.\d+)?)", img)
        if m and m.group(2) in EOL.get(m.group(1), {}):
            flags.append(f"{m.group(1)} {m.group(2)} : fin de support {EOL[m.group(1)][m.group(2)]} (table interne)")
    return {"images": images, "expose": re.findall(r"^\s*EXPOSE\s+(.+)$", text, re.M | re.I), "user": re.findall(r"^\s*USER\s+(\S+)", text, re.M | re.I)[-1:] or None,
            "cmd": (re.findall(r"^\s*(?:CMD|ENTRYPOINT)\s+(.+)$", text, re.M | re.I) or [None])[-1], "stages": len(images), "eol": flags}


def parse_compose(rel: str, path: str) -> dict:
    data = _yaml(_read(path))
    services = data.get("services") if isinstance(data, dict) else None
    out = []
    for n, s in (services or {}).items():
        if isinstance(s, dict):
            out.append({"name": n, "image": s.get("image"), "build": bool(s.get("build")), "ports": [str(p) for p in (s.get("ports") or [])][:6],
                        "depends_on": list(s.get("depends_on") or [])[:8] if not isinstance(s.get("depends_on"), dict) else list(s["depends_on"])[:8]})
    return {"services": out}


def parse_workflow(rel: str, path: str) -> dict:
    data = _yaml(_read(path))
    if not isinstance(data, dict):
        return {}
    on = data.get("on", data.get(True))  # YAML lit « on: » comme booléen True
    triggers = list(on) if isinstance(on, (dict, list)) else [on]
    jobs = data.get("jobs") or {}
    uses = sorted({str(st["uses"]).split("@")[0] for j in jobs.values() if isinstance(j, dict) for st in (j.get("steps") or []) if isinstance(st, dict) and st.get("uses")})
    return {"name": data.get("name"), "triggers": [str(t) for t in triggers][:6], "jobs": list(jobs)[:12], "actions": uses[:15]}


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #
def _walk(root: str):
    deadline, n = time.monotonic() + TIME_BUDGET, 0
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        depth = dirpath.count(os.sep) - base_depth
        dirnames[:] = [d for d in dirnames if d not in T.SKIP_DIRS and d not in EXTRA_SKIP] if depth < MAX_DEPTH else []
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            n += 1
            yield full, os.path.relpath(full, root), n > MAX_FILES or time.monotonic() > deadline
        if n > MAX_FILES or time.monotonic() > deadline:
            return


def scan_sync(root: str) -> dict:
    files: list[tuple[str, str]] = []
    truncated = False
    for full, rel, over in _walk(root):
        if over:
            truncated = True
            break
        files.append((full, rel))

    langs: Counter = Counter()
    loc: Counter = Counter()
    projects: dict[str, dict] = {}
    infra: dict[str, list] = {"dockerfiles": [], "compose": [], "ci": [], "kubernetes": [], "terraform": [], "makefiles": [], "other": []}
    data_hints: dict[str, list] = {"migrations": [], "sqlite_files": [], "prisma": [], "sql_files": 0}
    tests = {"files": 0, "dirs": set()}
    tooling: list[str] = []
    docs: list[str] = []
    env_example: dict[str, list] = {}
    env_files: list[str] = []
    env_code: Counter = Counter()
    secrets: list[dict] = []
    entrypoints: list[dict] = []
    gitignore = _read(os.path.join(root, ".gitignore"))
    src_budget = 4000

    for full, rel in files:
        base, ext = os.path.basename(rel), os.path.splitext(rel)[1].lower()
        parts = rel.split(os.sep)
        if ext in LANG:
            langs[LANG[ext]] += 1
            if src_budget > 0:
                src_budget -= 1
                try:
                    if os.path.getsize(full) <= 300_000:
                        loc[LANG[ext]] += _read(full, 300_000).count("\n")
                except OSError:
                    pass
        if base == "Dockerfile" or base.startswith("Dockerfile.") or base.endswith(".dockerfile"):
            infra["dockerfiles"].append({"file": rel, **parse_dockerfile(rel, full)})
        elif re.match(r"^(docker-)?compose(\.[\w-]+)?\.ya?ml$", base):
            infra["compose"].append({"file": rel, **parse_compose(rel, full)})
        elif rel.startswith(".github/workflows/") and ext in (".yml", ".yaml"):
            infra["ci"].append({"file": rel, "system": "GitHub Actions", **parse_workflow(rel, full)})
        elif base in (".gitlab-ci.yml", "Jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".travis.yml") or rel == ".circleci/config.yml":
            infra["ci"].append({"file": rel, "system": base})
        elif ext == ".tf":
            infra["terraform"].append(rel)
        elif base == "Makefile":
            targets = [t for t in re.findall(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*):(?!=)", _read(full), re.M)][:25]
            infra["makefiles"].append({"file": rel, "targets": targets})
        elif base in ("Procfile", "Chart.yaml", "skaffold.yaml", "serverless.yml", "vercel.json", "netlify.toml", "fly.toml", "render.yaml"):
            infra["other"].append(rel)
        elif ext in (".yaml", ".yml") and any(p in ("k8s", "kubernetes", "manifests", "helm", "charts", "deploy") for p in parts):
            kinds = sorted(set(re.findall(r"^kind:\s*(\w+)", _read(full, 50_000), re.M)))
            if kinds:
                infra["kubernetes"].append({"file": rel, "kinds": kinds[:6]})

        if base in MANIFESTS or (base.startswith("requirements") and base.endswith(".txt")):
            eco = MANIFESTS.get(base, "python")
            pdir = os.path.dirname(rel) or "."
            proj = projects.setdefault(pdir, {"path": pdir, "ecosystems": {}, "manifests": [], "lockfiles": []})
            parser = PARSE_BY_FILE.get(base) or PARSERS[eco]
            parsed = parser(rel, full) if base not in ("setup.py", "setup.cfg", "Pipfile") else {"runtime": [], "dev": []}
            parsed["manifest"] = rel
            proj["ecosystems"].setdefault(eco, []).append(parsed)
            proj["manifests"].append(rel)
        elif base in LOCKFILES:
            projects.setdefault(os.path.dirname(rel) or ".", {"path": os.path.dirname(rel) or ".", "ecosystems": {}, "manifests": [], "lockfiles": []})["lockfiles"].append(base)
        elif base == "csproj" or ext == ".csproj":
            pdir = os.path.dirname(rel) or "."
            proj = projects.setdefault(pdir, {"path": pdir, "ecosystems": {}, "manifests": [], "lockfiles": []})
            pk = [{"name": n, "spec": v} for n, v in re.findall(r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"', _read(full))]
            proj["ecosystems"].setdefault("dotnet", []).append({"runtime": pk, "dev": [], "manifest": rel})
            proj["manifests"].append(rel)

        if re.match(r"^(test_.*\.py|.*_test\.py|.*\.(test|spec)\.[jt]sx?|.*Test\.java|.*_test\.go)$", base):
            tests["files"] += 1
            tests["dirs"].add(os.path.dirname(rel) or ".")
        if base in (".eslintrc", ".eslintrc.json", ".eslintrc.js", "eslint.config.js", "eslint.config.mjs", ".prettierrc", ".editorconfig", "ruff.toml", ".ruff.toml", ".flake8",
                    "mypy.ini", "pyrightconfig.json", ".pre-commit-config.yaml", "tox.ini", "pytest.ini", ".coveragerc", "renovate.json", ".github/dependabot.yml", "tsconfig.json",
                    ".golangci.yml", "sonar-project.properties") or rel == ".github/dependabot.yml":
            tooling.append(rel)
        if re.match(r"^(README|CONTRIBUTING|CHANGELOG|LICENSE|SECURITY|AGENTS|CLAUDE|DECISIONS|ARCHITECTURE)(\.\w+)?$", base, re.I) or (parts[0] in ("docs", "doc", "adr") and ext == ".md" and len(docs) < 40):
            docs.append(rel)
        if any(p in ("migrations", "alembic", "flyway", "liquibase") for p in parts[:-1]) and ext in (".py", ".sql", ".xml", ".yaml", ".yml"):
            data_hints["migrations"].append(rel)
        elif base in ("alembic.ini", "schema.prisma"):
            data_hints["prisma" if base == "schema.prisma" else "migrations"].append(rel)
        if ext in (".db", ".sqlite", ".sqlite3"):
            data_hints["sqlite_files"].append(rel)
        if ext == ".sql":
            data_hints["sql_files"] += 1

        # .env : présence seulement. Modèles (.env.example) : NOMS de variables uniquement.
        if base.startswith(".env"):
            if base in (".env.example", ".env.sample", ".env.template", ".env.dist"):
                env_example[rel] = re.findall(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", _read(full, 50_000), re.M)[:80]
            else:
                env_files.append(rel)
            continue
        if ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".java", ".go", ".rb", ".php", ".sh", ".yml", ".yaml", ".json", ".toml", ".cfg", ".ini", ".properties", ".tf") \
                and src_budget > -3000:
            src_budget -= 1
            try:
                if os.path.getsize(full) > MAX_READ:
                    continue
            except OSError:
                continue
            text = _read(full)
            if ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".java", ".go", ".rb", ".php", ".sh"):
                if not _is_test_path(rel):  # variables des tests = fixtures, pas de la configuration
                    for v in ENV_CODE.findall(text):
                        env_code[v] += 1
                if ext == ".py":
                    for label, rx in (("FastAPI", r"\bFastAPI\("), ("Flask", r"\bFlask\(__name__"), ("uvicorn.run", r"\buvicorn\.run\("), ("main", r'if __name__ == ["\']__main__["\']')):
                        if re.search(rx, text) and len(entrypoints) < 25:
                            entrypoints.append({"kind": label, "file": rel})
            if len(secrets) < 20 and base != "package-lock.json":
                for label, rx in SECRET_PATTERNS.items():
                    if rx.search(text):
                        secrets.append({"file": rel, "type": label, **({"in_tests": True} if _is_test_path(rel) else {})})
                        break

    # --- consolidation par projet
    out_projects = []
    for pdir, p in sorted(projects.items()):
        runtime, dev, runtime_info = [], [], {}
        for eco, parsed_list in p["ecosystems"].items():
            for parsed in parsed_list:
                for d in parsed.get("runtime", []):
                    runtime.append({**d, "ecosystem": "pypi" if eco == "python" else "npm" if eco == "node" else eco, "evidence": parsed["manifest"]})
                for d in parsed.get("dev", []):
                    dev.append({**d, "ecosystem": "pypi" if eco == "python" else "npm" if eco == "node" else eco, "evidence": parsed["manifest"]})
                runtime_info.update({k: v for k, v in parsed.items() if k not in ("runtime", "dev", "manifest") and v})
        names = {_norm(d["name"]) for d in runtime + dev}
        frameworks = sorted({f"{CATALOG[n][1]} ({CATALOG[n][0]})" for n in names if n in CATALOG})
        flags = []
        rp = str(runtime_info.get("requires_python") or "")
        m = re.search(r"(\d+\.\d+)", rp)
        if m and m.group(1) in EOL["python"]:
            flags.append(f"python {m.group(1)} (plancher déclaré) : fin de support {EOL['python'][m.group(1)]} (table interne)")
        m = re.search(r"(\d+)", str(runtime_info.get("requires_node") or ""))
        if m and m.group(1) in EOL["node"]:
            flags.append(f"node {m.group(1)} (plancher déclaré) : fin de support {EOL['node'][m.group(1)]} (table interne)")
        keep = lambda ds, n: [{k: v for k, v in d.items() if k != "evidence"} for d in ds[:n]]
        out_projects.append({"path": pdir, "ecosystems": sorted(p["ecosystems"]), "manifests": p["manifests"], "lockfiles": sorted(set(p["lockfiles"])),
                             "reproducible_install": bool(p["lockfiles"]), "runtime_info": runtime_info, "frameworks": frameworks, "eol_flags": flags,
                             "dependencies": keep(runtime, 60), "dev_dependencies": keep(dev, 30),
                             "dependency_count": {"runtime": len(runtime), "dev": len(dev)}})

    compose_images = [s.get("image") for c in infra["compose"] for s in c["services"] if s.get("image")]
    compose_images += [i for d in infra["dockerfiles"] for i in d["images"]]
    services = sorted({label for img in compose_images for key, label in IMAGE_DB.items() if key in str(img).lower()})
    dep_names = {_norm(d["name"]) for p in out_projects for d in p["dependencies"] + p["dev_dependencies"]}
    db_from_deps = sorted({CATALOG[n][1] for n in dep_names if n in CATALOG and CATALOG[n][0] in ("db-driver", "cache/queue", "orm", "migrations")})
    env_declared = sorted({v for vs in env_example.values() for v in vs})
    env_used = [v for v, _ in env_code.most_common(80) if v not in SYSTEM_ENV]
    env_system = sorted(v for v in env_code if v in SYSTEM_ENV)
    git_ignored_env = bool(re.search(r"^\s*(\.env\*?|\*\.env|\.env\.\*)\s*$", gitignore, re.M))
    return {
        "root": os.path.relpath(root, T.get_workdir()) or ".", "files_scanned": len(files), "truncated": truncated,
        "languages": [{"language": l, "files": n, "approx_loc": loc.get(l, 0)} for l, n in langs.most_common(8)],
        "projects": out_projects,
        "entrypoints": entrypoints,
        "infrastructure": {k: v for k, v in infra.items() if v} | ({"service_images": services} if services else {}),
        "data": {"db_clues": sorted(set(db_from_deps) | set(services) & {"PostgreSQL", "MySQL", "MariaDB", "MongoDB", "Redis", "Elasticsearch", "OpenSearch", "ClickHouse", "Qdrant"}),
                 "migrations": data_hints["migrations"][:10], "prisma": data_hints["prisma"], "sqlite_files": data_hints["sqlite_files"][:10], "sql_files": data_hints["sql_files"]},
        "tests": {"files": tests["files"], "directories": sorted(tests["dirs"])[:10]},
        "tooling": sorted(set(tooling)),
        "docs": docs[:30],
        "environment": {"declared_in_examples": env_declared[:80], "used_in_code": env_used, "system_vars_used": env_system,
                        "undeclared_but_used": sorted(set(env_used) - set(env_declared))[:40] if env_declared else [],
                        "dotenv_files_present_NOT_READ": env_files, "dotenv_gitignored": git_ignored_env if env_files else None},
        "possible_secrets_heuristic": secrets,
    }


async def _git_info(root: str) -> dict:
    from claude_clone import agent_tools as AT
    if not os.path.isdir(os.path.join(T.get_workdir(), ".git")):
        return {"is_repo": False}
    info: dict = {"is_repo": True}
    for key, args in (("last_commit", ["log", "-1", "--format=%cs"]), ("commits", ["rev-list", "--count", "HEAD"]), ("branch", ["rev-parse", "--abbrev-ref", "HEAD"])):
        try:
            info[key] = (await AT._git(*args)).strip()
        except ToolException:
            pass
    try:
        remotes = (await AT._git("remote", "-v")).splitlines()
        info["remotes"] = sorted({re.sub(r"//[^@/]*@", "//", ln.split()[1]) for ln in remotes if len(ln.split()) > 1})[:3]  # identifiants retirés
    except ToolException:
        pass
    return info


async def add_updates(result: dict, limit: int = 30) -> None:
    """Compare les dépendances DIRECTES (PyPI / npm) à leur dernière version publiée. Réseau : web.http_get (garde SSRF, proxy)."""
    from packaging.version import InvalidVersion, Version
    todo, seen = [], set()
    for p in result["projects"]:
        for d in p["dependencies"]:
            key = (d["ecosystem"], d["name"].lower())
            if d["ecosystem"] in ("pypi", "npm") and key not in seen and len(todo) < limit:
                seen.add(key)
                todo.append((p, d))
    gate = asyncio.Semaphore(8)

    async def one(d: dict) -> None:
        async with gate:
            try:
                if d["ecosystem"] == "pypi":
                    _, body = await web.http_get(f"https://pypi.org/pypi/{d['name']}/json")
                    try:
                        latest = json.loads(body)["info"]["version"]
                    except ValueError:  # JSON tronqué (gros paquets : pydantic ≈ 1,8 Mo) : info.version est toute en tête du document
                        m = re.search(r'"version"\s*:\s*"([^"]+)"', body)
                        if not m:
                            raise ToolException("réponse PyPI illisible")
                        latest = m.group(1)
                else:
                    _, body = await web.http_get(f"https://registry.npmjs.org/{d['name'].replace('/', '%2f')}/latest")
                    latest = json.loads(body)["version"]
            except (ToolException, ValueError, KeyError, TypeError) as exc:
                d["latest"], d["behind"] = None, f"inconnu ({str(exc)[:50]})"
                return
            d["latest"] = latest
            spec = str(d.get("spec", ""))
            m = re.search(r"(\d+(?:\.\d+){0,2})", spec)
            d["pinned"] = bool(re.match(r"^\s*(==|=)?\s*\d|^\s*\d", spec)) and not re.match(r"^\s*[\^~><]", spec)
            if not m:
                d["behind"] = "non borné"
                return
            try:
                cur, new = Version(m.group(1)), Version(latest)
            except InvalidVersion:
                d["behind"] = "inconnu"
                return
            d["behind"] = "major" if new.major > cur.major else "minor" if new.minor > cur.minor else "patch" if new.micro > cur.micro else "à jour"
            if not d["pinned"] and d["behind"] != "à jour":
                d["behind"] += " (plancher de plage, pas forcément la version installée)"

    await asyncio.gather(*(one(d) for _, d in todo))
    result["updates_checked"] = len(todo)


async def scan_stack_impl(path: str = ".", check_updates: bool = False) -> str:
    root = T.safe_path(path or ".")
    if not os.path.isdir(root):
        raise ToolException(f"Dossier introuvable : {path}")
    result = await asyncio.to_thread(scan_sync, root)
    result["git"] = await _git_info(root)
    if check_updates:
        await add_updates(result)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=list)


@tool("scan_stack")
async def scan_stack(path: str = ".", check_updates: bool = False) -> str:
    """Scanner DÉTERMINISTE de la stack d'un dossier du projet (lecture seule, sans LLM) : langages, manifests et dépendances par
    sous-projet, frameworks, Docker/compose/CI/Terraform/K8s, migrations et bases, tests, outils qualité, points d'entrée, variables
    d'environnement (noms seulement : les .env ne sont JAMAIS lus), secrets possibles (heuristique, sans valeur), git. Chaque fait
    porte son fichier-preuve. `check_updates=true` compare les ~30 dépendances directes PyPI/npm à leur dernière version publiée."""
    return await scan_stack_impl(path, check_updates)
