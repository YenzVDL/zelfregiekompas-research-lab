"""Artifact-aware thinking controls; tags alone never establish capabilities."""


def describe(info):
    metadata = info.get("model_info") or {}
    controls = info.get("thinking") or {}
    values = controls.get("values")
    finetune = str(metadata.get("general.finetune", ""))
    version = str(metadata.get("general.version", ""))
    if (isinstance(values, list) and values and False not in values) or (finetune.lower() == "thinking" and version == "2507"):
        variant = "thinking-only"
    elif isinstance(values, list) and False in values and True in values:
        variant = "hybrid"
    elif "thinking" not in info.get("capabilities", []) or values == [False]:
        variant = "instruct/non-thinking" if "instruct" in finetune.lower() else "non-thinking"
    else:
        variant = "thinking controls unknown"
    return {"variant": variant, "family": (info.get("details") or {}).get("family") or metadata.get("general.architecture"),
            "parameter_size": (info.get("details") or {}).get("parameter_size"), "finetune": finetune or None,
            "artifact_version": version or None, "thinking_values": values, "thinking_default": controls.get("default"),
            "source": "Installed Ollama /api/show thinking and model_info; no inference from parameter-size tag"}


def validate_control(info, think):
    identity = describe(info)
    values = identity["thinking_values"]
    if think is None:
        return identity
    if values is not None and not any(type(think) is type(value) and think == value for value in values):
        raise ValueError(f"Unsupported think={think!r} for {info.get('name', 'installed artifact')}: supported controls are {values}. Select a compatible model or an explicit supported thinking mode.")
    if identity["variant"] == "thinking-only" and think is False:
        raise ValueError("This installed Thinking-2507/thinking-only artifact cannot disable thinking. Explicitly install/select an instruct variant, or configure a thinking experiment.")
    if identity['variant'] == 'thinking controls unknown' and think is not None:
        raise ValueError('Cannot verify this thinking control from installed artifact metadata. Refresh/update Ollama metadata or select a verified compatible model; the requested control will not be assumed supported.')
    return identity


def validate_research(info, settings, mode="short"):
    identity = validate_control(info, settings.get("think"))
    if mode == "short":
        if identity["variant"] in ["thinking-only", "thinking controls unknown"]:
            raise ValueError(f"{info.get('name')}: {identity['variant']} is incompatible with short reflection mode. Select a verified instruct/non-thinking model; qwen3:4b-instruct is a separate explicit installation, or choose Thinking experiment mode.")
        if identity["variant"] == "hybrid" and settings.get("think") is not False:
            raise ValueError(f"{info.get('name')}: short reflection mode requires explicit think=false for this hybrid artifact. Saved settings will not be changed automatically.")
        if settings.get("think") not in [None, False]:
            raise ValueError("Short reflection mode does not enable thinking; choose Thinking experiment mode explicitly.")
    elif mode == "thinking":
        if identity['variant'] in ['non-thinking','instruct/non-thinking']:
            raise ValueError('This installed artifact does not advertise thinking support; choose a verified thinking-capable model for an intentional thinking experiment.')
        if not (settings.get("think") is True or isinstance(settings.get("think"), str)):
            raise ValueError("Thinking experiments require an explicit supported think=true or named level.")
        if settings.get("num_predict", 0) < 1024:
            raise ValueError("Thinking experiments require an intentional output budget of at least 1024 tokens (thinking and final answer share it). This minimum is not a guarantee of completion.")
        if settings.get('num_ctx',4096) < settings['num_predict'] + 1024:
            raise ValueError('Thinking experiments need num_ctx at least num_predict + 1024 for prompt space. Choose both budgets explicitly and run preflight; no setting is raised automatically.')
    return identity


def classify(text, runtime, errors):
    limited = runtime.get("done_reason") == "length"
    if not text.strip():
        return "output_limit_before_answer" if limited else "empty_final_answer"
    if errors:
        return "truncated" if limited else "invalid"
    return "completed"
