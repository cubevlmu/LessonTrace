#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

usage() {
    echo '用法: bash start_product.sh [--no-browser] [--build] [--port 8787]'
    echo '启动本机产品页面和分析队列，按 Ctrl+C 停止。'
}
port=8787
open_browser=true
build=false
while (($#)); do
    case "$1" in
        --no-browser) open_browser=false ;;
        --build) build=true ;;
        --port) port=${2:?--port 需要端口号}; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done
if [[ ! $port =~ ^[1-9][0-9]{0,4}$ ]] || ((port > 65535)); then
    echo '端口号须在 1–65535 之间。' >&2; exit 2
fi

export INTICLASS_PROFILE=product INTICLASS_EXTERNAL_WORKER=1
export INTICLASS_DATA_DIR="${INTICLASS_DATA_DIR:-$PWD/lesson_trace_data}"
mkdir -p -- "$INTICLASS_DATA_DIR"
exec 9>"$INTICLASS_DATA_DIR/.linux-launch.lock"
flock -n 9 || { echo '该数据目录已有 Linux 启动器在运行。' >&2; exit 1; }

if [[ -n ${INTICLASS_APP_PYTHON:-} ]]; then
    python=$INTICLASS_APP_PYTHON
elif [[ -n ${VIRTUAL_ENV:-} && -x $VIRTUAL_ENV/bin/python ]]; then
    python=$VIRTUAL_ENV/bin/python
elif [[ -x .venv/bin/python ]]; then
    python=.venv/bin/python
elif command -v uv >/dev/null; then
    cache="${XDG_CACHE_HOME:-$HOME/.cache}/lessontrace"
    mkdir -p -- "$cache"
    venv="$cache/python312"
    python="$venv/bin/python"
    (
        flock 8
        [[ -x $python ]] || uv venv --python 3.12 "$venv"
        uv pip install --python "$python" -r requirements/api.txt
    ) 8>"$cache/python312.lock"
else
    python=python3
fi
if ! "$python" -c 'import uvicorn, deploy_app'; then
    echo '请在 Linux Python 虚拟环境安装 requirements/api.txt，或安装 uv 后重新运行。' >&2
    exit 1
fi

if [[ $build = true || ! -s frontend/dist/index.html ]]; then
    if command -v pnpm >/dev/null; then
        pnpm_cmd=(pnpm)
    elif command -v corepack >/dev/null; then
        pnpm_cmd=(corepack pnpm)
    else
        manager=$("$python" -c 'import json; print(json.load(open("frontend/package.json"))["packageManager"])')
        pnpm_cmd=(npx --yes "$manager")
    fi
    (cd frontend && "${pnpm_cmd[@]}" install --frozen-lockfile && "${pnpm_cmd[@]}" run build)
fi

"$python" - "$port" <<'PY'
import socket, sys
with socket.socket() as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('127.0.0.1', int(sys.argv[1])))
    except OSError as error:
        sys.exit(f'无法监听端口 {sys.argv[1]}: {error}')
PY
"$python" scripts/initialize_product.py --local-linux
unset INTICLASS_ADMIN_PASSWORD

weights="$INTICLASS_DATA_DIR/weights"
[[ -f "$weights/pretrained/yolo11s.onnx" ]] && detector="$weights/pretrained/yolo11s.onnx" || detector="$PWD/deploy/models/yolo11s.onnx"
[[ -d "$weights/faces" ]] && faces="$weights/faces" || faces="$PWD/deploy/models/faces"
export INTICLASS_PERSON_WEIGHTS="${INTICLASS_PERSON_WEIGHTS:-$detector}"
export INTICLASS_FACE_MODEL_DIR="${INTICLASS_FACE_MODEL_DIR:-$faces}"

cached_tracker="${XDG_CACHE_HOME:-$HOME/.cache}/lessontrace/tracking312/bin/python"
if [[ -z ${INTICLASS_TRACKING_PYTHON:-} && ! -x .venv-tracking/bin/python && -x $cached_tracker ]]; then
    export INTICLASS_TRACKING_PYTHON="$cached_tracker"
fi

api_pid=''
worker_pid=''
model_pid=''
stop_children() {
    if [[ -n $worker_pid ]]; then
        kill -TERM -- "-$worker_pid" 2>/dev/null || kill -TERM "$worker_pid" 2>/dev/null || :
    fi
    if [[ -n $api_pid ]]; then kill -TERM "$api_pid" 2>/dev/null || :; fi
    if [[ -n $model_pid ]]; then kill -TERM "$model_pid" 2>/dev/null || :; fi
    wait || :
}
trap stop_children EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Reuse an existing model service, or start the installed local copy for this session.
if [[ ${INTICLASS_VLM_URL:-http://127.0.0.1:11434/v1} == http://127.0.0.1:11434/v1 ]] &&
   ! curl --noproxy '*' -fs --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    ollama_bin=$(command -v ollama || :)
    if [[ -z $ollama_bin ]]; then
        ollama_bin="${XDG_CACHE_HOME:-$HOME/.cache}/lessontrace/ollama-runtime/ollama"
    fi
    if [[ -x $ollama_bin ]]; then
        OLLAMA_HOST=127.0.0.1:11434 "$ollama_bin" serve 9>&- &
        model_pid=$!
    fi
fi
url="http://127.0.0.1:$port"
"$python" -m uvicorn deploy_app:create_app --factory --host 127.0.0.1 --port "$port" --no-access-log 9>&- &
api_pid=$!
for ((attempt=0; attempt<120; attempt++)); do
    kill -0 "$api_pid" 2>/dev/null || { echo 'API 启动失败，请查看上方日志。' >&2; exit 1; }
    if curl --noproxy '*' -fs --max-time 1 "$url/app" >/dev/null; then break; fi
    sleep 0.5
done
((attempt < 120)) || { echo '等待产品页面就绪超时。' >&2; exit 1; }

setsid "$python" -u scripts/product_worker.py 9>&- &
worker_pid=$!
echo "LessonTrace: $url/app （按 Ctrl+C 停止）"
if [[ $open_browser = true && -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]] && command -v xdg-open >/dev/null; then
    # A desktop browser may stay alive after opening the URL.
    xdg-open "$url/app" 9>&- >/dev/null 2>&1 &
    disown "$!"
fi
wait -n "$api_pid" "$worker_pid"
# Both services are long-running. If either exits, stop its peer.
echo '产品服务已退出。' >&2
exit 1
