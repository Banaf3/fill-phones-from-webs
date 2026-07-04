from deliverect_sync.models import RunStatus


def test_run_status_enum_contains_expected_values() -> None:
    assert RunStatus.EXPORT_FAILED.value == "EXPORT_FAILED"
    assert RunStatus.SUCCESS.value == "SUCCESS"
    assert RunStatus.AUTH_REQUIRED.value == "AUTH_REQUIRED"
