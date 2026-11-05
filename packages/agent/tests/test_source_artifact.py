import pytest
from agent.checker_spec import SourceArtifact


def test_code_identity_does_not_depend_on_input_but_each_binding_requires_validation():
    source = SourceArtifact(files={"checker.py": b"def create_audit(*args): pass"})
    first = source.bind(input_digest="a" * 64, executor_revision="runtime", validated_program_digest=source.program_digest, validated_input_digest="a" * 64)
    second = source.bind(input_digest="b" * 64, executor_revision="runtime", validated_program_digest=source.program_digest, validated_input_digest="b" * 64)
    assert first.program_digest == second.program_digest == source.program_digest
    assert first.input_digest != second.input_digest
    with pytest.raises(ValueError, match="requires validation"):
        source.bind(input_digest="b" * 64, executor_revision="runtime", validated_program_digest=source.program_digest, validated_input_digest="a" * 64)
    with pytest.raises(TypeError):
        source.files["checker.py"] = b"different"
    assert SourceArtifact.from_artifact(first).model_dump() == source.model_dump()
