"""Attempt 3 freeze-2 and freeze-3 (2026-09-14): what depends on the served model is derived from its served name
(pod/model-env.sh). Freeze-1 hardcoded Qwen's snapshot path in run.sh, so vLLM would have served Qwen whatever model the
capable arm named. Freeze-3 separates the served name from the repository: gpt-oss-120b is served unprefixed, because under
`openai/gpt-oss-120b` the OpenHands adapter's requests reached vLLM as `gpt-oss-120b` and every call was a 404.
Qwen's served name, path and serving arguments must be byte-identical to freeze-1's, since the small-arm smoke ran on them."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mark_platform.serving import parse_serve_args

POD = Path(__file__).resolve().parents[1] / "pod"
PINS = Path(__file__).resolve().parents[3] / "benchmarks" / "model-pins"
BASH = shutil.which("bash")
QWEN = "Qwen/Qwen2.5-7B-Instruct-AWQ"
GPT_OSS = "gpt-oss-120b"                 # the served name
GPT_OSS_REPO = "openai/gpt-oss-120b"     # the repository its pinned snapshot comes from
QWEN3 = "Qwen/Qwen3-32B"                  # freeze-4: bf16, did not start at 32,768 (KV cache 7.91 GiB < 8.0 GiB)
QWEN3_FP8 = "Qwen/Qwen3-32B-FP8"          # freeze-5: the capable arm, "Qwen3-32B (FP8)", a precision change
# run.sh's MARK_SERVING_ARGS default at attempt3-freeze-1 (169ab5f), the arguments the small-arm smoke's engine log confirms
QWEN_FREEZE_1 = "--max-model-len 32768 --enable-auto-tool-choice --tool-call-parser hermes --seed 7 --gpu-memory-utilization 0.85 --max-num-seqs 1024 --no-enable-prefix-caching"


def _env(fn, *args):
    r = subprocess.run([BASH, "-c", 'source "$1"; shift; "$@"', "_", (POD / "model-env.sh").as_posix(), fn, *args], capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout.strip()


pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def test_serving_arguments_are_pinned_per_served_name_and_qwens_are_unchanged():
    code, qwen = _env("serving_args_for", QWEN)
    assert code == 0 and qwen == QWEN_FREEZE_1
    code, oss = _env("serving_args_for", GPT_OSS)
    parsed = parse_serve_args(oss)
    assert code == 0 and parsed["tool_call_parser"] == "openai" and parsed["gpu_memory_utilization"] == 0.95 and parsed["max_model_len"] == 32768
    assert parsed["seed"] == 7 and parsed["max_num_seqs"] == 1024 and parsed["enable_prefix_caching"] is False and parsed["enable_auto_tool_choice"] is True
    assert "--max-num-batched-tokens 1024" in oss
    # freeze-4: Qwen3-32B, the capable arm. hermes as the small arm, thinking off server-side through the chat template (a
    # pre-registered request setting), chunked prefill at vLLM's default as the small arm
    code, q3 = _env("serving_args_for", QWEN3)
    parsed3 = parse_serve_args(q3)
    assert code == 0 and parsed3["tool_call_parser"] == "hermes" and parsed3["max_model_len"] == 32768 and parsed3["seed"] == 7 and parsed3["max_num_seqs"] == 1024
    assert parsed3["enable_prefix_caching"] is False and parsed3["gpu_memory_utilization"] == 0.95 and "--max-num-batched-tokens" not in q3
    assert '--default-chat-template-kwargs {"enable_thinking":false}' in q3
    # run.sh expands the arguments unquoted: the JSON must stay one word, quotes intact, for vLLM's json.loads
    r = subprocess.run([BASH, "-c", 'source "$1"; set -- $(serving_args_for "$2"); for a; do printf "%s\\n" "$a"; done', "_", (POD / "model-env.sh").as_posix(), QWEN3],
                       capture_output=True, text=True, timeout=30)
    words = r.stdout.splitlines()
    assert words[words.index("--default-chat-template-kwargs") + 1] == '{"enable_thinking":false}', words
    assert json.loads(words[words.index("--default-chat-template-kwargs") + 1]) == {"enable_thinking": False}
    # freeze-5: the FP8 checkpoint changes precision only; every serving argument is the bf16 entry's
    assert _env("serving_args_for", QWEN3_FP8) == (0, q3)
    # a model without pinned arguments gets none, and run.sh refuses to start vLLM for it; the prefixed name is not a served name
    assert _env("serving_args_for", "someone/unpinned-model") == (1, "")
    assert _env("serving_args_for", GPT_OSS_REPO) == (1, ""), "served as openai/gpt-oss-120b, the OpenHands requests reached vLLM under another name"


def test_the_served_name_maps_to_its_repository_and_the_cache_path_follows_the_repository():
    assert _env("model_repo_for", QWEN) == (0, QWEN)
    assert _env("model_repo_for", GPT_OSS) == (0, GPT_OSS_REPO)
    assert _env("model_repo_for", GPT_OSS_REPO)[0] == 1
    assert _env("model_repo_for", QWEN3) == (0, QWEN3)
    assert _env("model_cache_dir", "/opt/mark", QWEN3) == (0, "/opt/mark/hf/hub/models--Qwen--Qwen3-32B")
    assert _env("model_repo_for", QWEN3_FP8) == (0, QWEN3_FP8)
    assert _env("model_cache_dir", "/opt/mark", QWEN3_FP8) == (0, "/opt/mark/hf/hub/models--Qwen--Qwen3-32B-FP8")
    assert _env("model_cache_dir", "/opt/mark", QWEN) == (0, "/opt/mark/hf/hub/models--Qwen--Qwen2.5-7B-Instruct-AWQ")
    assert _env("model_cache_dir", "/opt/mark", GPT_OSS_REPO) == (0, "/opt/mark/hf/hub/models--openai--gpt-oss-120b")
    run_sh = (POD / "run.sh").read_text(encoding="utf-8")
    assert "models--Qwen" not in run_sh, "a hardcoded snapshot path serves one model whatever the run names"
    assert run_sh.count('"$MODEL_CACHE"/snapshots/*/') == 2 and 'model_repo_for "$MARK_LLM_MODEL"' in run_sh
    assert 'model_repo_for "$MARK_LLM_MODEL"' in (POD / "model.sh").read_text(encoding="utf-8"), "model.sh refuses a repository that is not the served name's"


def test_the_download_is_exactly_the_pinned_files():
    qwen_pin = PINS / "Qwen2.5-7B-Instruct-AWQ-b25037543e9394b818fdfca67ab2a00ecc7dd641.sha256"
    oss_pin = PINS / "gpt-oss-120b-b5c939de8f754692c1647ca79fbf85e8c1e70f8a.sha256"
    code, qwen_paths = _env("pinned_paths", qwen_pin.as_posix())
    assert code == 0 and len(qwen_paths.splitlines()) == 12 and not any(p.startswith("./") for p in qwen_paths.splitlines())
    code, oss_paths = _env("pinned_paths", oss_pin.as_posix())
    paths = oss_paths.splitlines()
    assert code == 0 and len(paths) == 26 and "model-00000-of-00014.safetensors" in paths and "tokenizer.json" in paths
    assert not any(p.startswith(("metal/", "original/")) for p in paths), "the other copies of the weights are not pinned"
    assert "allow_patterns=paths" in (POD / "model.sh").read_text(encoding="utf-8")
    # the aggregate hash the README records is the pin list's own sha256 (the manifest's pins.model.hash)
    readme = (PINS / "README.md").read_text(encoding="utf-8")
    assert f"`{hashlib.sha256(oss_pin.read_bytes()).hexdigest()}`" in readme and f"`{hashlib.sha256(qwen_pin.read_bytes()).hexdigest()}`" in readme
    # freeze-4: Qwen3-32B pins every file of its repository (no second copy of the weights to leave out)
    q3_pin = PINS / "Qwen3-32B-9216db5781bf21249d130ec9da846c4624c16137.sha256"
    code, q3_paths = _env("pinned_paths", q3_pin.as_posix())
    paths3 = q3_paths.splitlines()
    assert code == 0 and len(paths3) == 27 and "model-00017-of-00017.safetensors" in paths3 and "vocab.json" in paths3 and "merges.txt" in paths3
    assert f"`{hashlib.sha256(q3_pin.read_bytes()).hexdigest()}`" in readme
    # freeze-5: Qwen3-32B-FP8, every file of its repository
    fp8_pin = PINS / "Qwen3-32B-FP8-aa55da1ecc13d006e8b8e4f54579b1ea8c3db2df.sha256"
    code, fp8_paths = _env("pinned_paths", fp8_pin.as_posix())
    paths_fp8 = fp8_paths.splitlines()
    assert code == 0 and len(paths_fp8) == 17 and "model-00007-of-00007.safetensors" in paths_fp8 and "vocab.json" in paths_fp8
    assert f"`{hashlib.sha256(fp8_pin.read_bytes()).hexdigest()}`" in readme
