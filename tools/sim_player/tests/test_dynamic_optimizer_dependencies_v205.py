# test_dynamic_optimizer_dependencies_v205.py ver 1
"""Regression for v205: Analyzer-managed generation must ship its replay dependencies."""

def test_dynamic_optimizer_runtime_dependency_chain_imports():
    from tools.nl2_sync_benchmark.pipeline import Pipeline, NoNetwork
    from tools.nl2_sync_benchmark.runner import packets
    from tools.nl2_sync.host import host_details
    from tools.nl2_dynamic_profiles.replay import prepare, verify_production
    assert Pipeline is not None and NoNetwork is not None
    assert callable(packets) and callable(host_details)
    assert callable(prepare) and callable(verify_production)
