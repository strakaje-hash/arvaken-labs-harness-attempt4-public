# Sourced by run.sh and model.sh (attempt 3 freeze-2 and freeze-3, 2026-09-14): everything on a pod that depends on the served
# model is derived from its served name, MARK_LLM_MODEL. Freeze-1 hardcoded Qwen's snapshot path, so vLLM served Qwen whatever
# MARK_LLM_MODEL said.
#
# Freeze-3: the served name is separate from the Hugging Face repository. gpt-oss-120b is served as `gpt-oss-120b`. Under
# `openai/gpt-oss-120b` the OpenHands adapter's model string, `openai/openai/gpt-oss-120b`, reached vLLM as `gpt-oss-120b`, so
# every call was a 404 (capable-arm smoke on freeze-2). That is an OPEN ITEM, not a resolved one: LiteLLM's own provider
# parsing yields `openai/gpt-oss-120b`, so a second prefix strip happens somewhere in the SDK-to-LiteLLM path, and that line has
# not been found (docs/POD.md). The unprefixed served name avoids it; it does not characterize it.

model_repo_for() {     # model_repo_for <served name>: the Hugging Face repository its pinned snapshot comes from
  case "$1" in
    Qwen/Qwen2.5-7B-Instruct-AWQ) echo "Qwen/Qwen2.5-7B-Instruct-AWQ" ;;
    gpt-oss-120b) echo "openai/gpt-oss-120b" ;;
    Qwen/Qwen3-32B) echo "Qwen/Qwen3-32B" ;;
    Qwen/Qwen3-32B-FP8) echo "Qwen/Qwen3-32B-FP8" ;;
    *) return 1 ;;
  esac
}

# Serving arguments per served name (founder rulings 2026-09-12 and 2026-09-14). mark_platform.serving records them from three
# sources and pins the effective condition; where the arms differ, the model requires it and the report states it. A model with
# no entry here has no pinned arguments, and vLLM is not started for it.
serving_args_for() {   # serving_args_for <served name>
  case "$1" in
    Qwen/Qwen2.5-7B-Instruct-AWQ)
      echo "--max-model-len 32768 --enable-auto-tool-choice --tool-call-parser hermes --seed 7 --gpu-memory-utilization 0.85 --max-num-seqs 1024 --no-enable-prefix-caching" ;;
    gpt-oss-120b)
      # vLLM 0.29.0: gpt-oss tool calls come back through the harmony path with the `openai` parser (tool_choice auto only).
      # On one H100 the default memory settings run out of memory (vLLM's gpt-oss recipe), so 0.95 with chunked prefill at
      # 1024 batched tokens. max_num_batched_tokens is a condition field: Qwen runs at vLLM's default, 8192, and the arms'
      # reproducibility is not comparable across it. If 32,768 tokens has no headroom at 0.95, the choice is a smaller
      # context or the fallback model, never a higher utilization (founder ruling 2026-09-14). Measured on freeze-2: weights
      # 61.43 GiB, KV cache 9.44 GiB, 257,779 tokens, 7.87x concurrency at 32,768 tokens per request.
      # SET ASIDE for the capable arm (founder ruling 2026-09-14): a serving-stack limitation, not a model judgment. vLLM
      # 0.29.0's Harmony parser raised HTTP 500 on the model's own output in multi-turn agent conversations. The entry and
      # the pin stay, so a later arm can revisit it on a pinned vLLM version.
      echo "--max-model-len 32768 --enable-auto-tool-choice --tool-call-parser openai --seed 7 --gpu-memory-utilization 0.95 --max-num-batched-tokens 1024 --max-num-seqs 1024 --no-enable-prefix-caching" ;;
    Qwen/Qwen3-32B)
      # The capable arm (freeze-4, founder ruling 2026-09-14): official bf16 checkpoint, served as its repository name.
      # - hermes, as the small arm: one less variable between arms.
      # - Thinking OFF, pre-registered, set server-side through the chat template (vLLM 0.29.0 --default-chat-template-kwargs),
      #   so no target's request code changes. The two arms then differ in model capability only. A thinking-on arm is a
      #   third, later measurement. The JSON has no spaces because run.sh expands MARK_SERVING_ARGS unquoted.
      # - 0.95: about 61 GiB of weights on an 80 GiB card leaves about 9 GiB of KV cache, about 36,000 tokens at 64 layers x
      #   8 KV heads x 128 x bf16. If the smoke shows too little headroom at 32,768 tokens, the fallback is the official
      #   Qwen3-32B-FP8 checkpoint, recorded as such, never a higher utilization.
      # - Chunked prefill at vLLM's default, as the small arm (no --max-num-batched-tokens).
      # DID NOT START (capable-arm smoke on freeze-4, 2026-09-15): weights 61.03 GiB left 7.91 GiB of KV cache at 0.95. One
      # 32,768-token request needs 8.0 GiB; vLLM's estimated maximum was 32,384 tokens. Per the founder's rule the arm moves
      # to the official FP8 checkpoint below, recorded as a precision change. The entry stays as the record.
      echo "--max-model-len 32768 --enable-auto-tool-choice --tool-call-parser hermes --seed 7 --gpu-memory-utilization 0.95 --max-num-seqs 1024 --no-enable-prefix-caching --default-chat-template-kwargs {\"enable_thinking\":false}" ;;
    Qwen/Qwen3-32B-FP8)
      # The capable arm from freeze-5: "Qwen3-32B (FP8)" wherever the arm's model line appears. This is a precision change
      # from bf16: the official block-wise FP8 checkpoint (e4m3, 128x128 blocks, dynamic activations), same chat template.
      # Every other argument is the bf16 entry's: hermes, thinking off server-side, 0.95, 32,768 context, max_num_seqs 1024,
      # prefix caching off, seed 7, chunked prefill at vLLM's default. Weights about 34 GB leave far more KV cache than bf16
      # did; the smoke records how much and the concurrency it allows.
      echo "--max-model-len 32768 --enable-auto-tool-choice --tool-call-parser hermes --seed 7 --gpu-memory-utilization 0.95 --max-num-seqs 1024 --no-enable-prefix-caching --default-chat-template-kwargs {\"enable_thinking\":false}" ;;
    *) return 1 ;;
  esac
}

model_files_prefix() { # model_files_prefix <work dir> <repository id>: the prefix EVERY per-model artifact is named with
  # **Anything specific to one model is named by that model, or it does not get written** (founder ruling
  # 2026-09-22, after four instances of the class in one day). A per-model fact at a fixed path is overwritten by
  # the second model on the pod, and the failure is silent in one direction: model-hash.txt becomes pins.model.hash
  # in the SIGNED manifest, so a run could publish one model's fingerprint as the identity of another's weights.
  echo "$1/hf/${2//\//-}"
}

model_cache_dir() {    # model_cache_dir <work dir> <repository id>: the Hugging Face hub cache directory of that repository
  echo "$1/hf/hub/models--${2//\//--}"
}

pinned_paths() {       # pinned_paths <pin list>: the file paths a pin list names, one per line, without the leading ./
  cut -d'*' -f2- "$1" | sed 's#^\./##'
}
