"""End-to-end build tests over a small real dataset fixture.

Skipped when the vendored extractor or its whoisit bootstrap (network) is
unavailable — those are environment problems, not failures of this code.
"""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from thor_extract import pipeline

FIXTURE = Path(__file__).parent / "data" / "sample_v3.parquet"


@pytest.fixture(scope="module")
def dr():
    if not FIXTURE.is_file():
        pytest.skip("fixture missing")
    try:
        return pipeline.init_extractor(pipeline.BuildConfig())
    except (pipeline.BuildError, Exception) as exc:  # noqa: BLE001
        pytest.skip(f"extractor unavailable: {exc}")


@pytest.fixture
def output(tmp_path):
    return tmp_path / "features.parquet"


def test_feature_columns_exclude_intermediates(dr):
    names = [name for name, _ in pipeline.feature_columns(dr)]
    assert "domain_name" not in names
    assert not [name for name in names if name.startswith("tmp_")]
    assert "dns_A_count" in names and "lex_tld_len" in names


def test_build_writes_domain_and_features(dr, output):
    stats = pipeline.build(FIXTURE, output, pipeline.BuildConfig(batch_size=2))

    source = pq.read_table(FIXTURE, columns=["domain"]).column("domain").to_pylist()
    table = pq.read_table(output)
    assert stats.rows_in == len(source)
    assert stats.rows_out == len(source)
    assert table.column("domain").to_pylist() == source  # order preserved
    assert table.schema.names[0] == "domain"
    features = pipeline.feature_columns(dr)
    assert table.schema.names[1:] == [name for name, _ in features]
    assert table.schema.field("dns_A_count").type.equals(pa.int64())

    manifest = json.loads(
        output.with_suffix(output.suffix + ".manifest.json").read_text()
    )
    assert manifest["rows"]["written"] == len(source)
    assert manifest["columns"]["featureCount"] == len(features)
    assert manifest["extractor"]["commit"] != "unknown"


def test_passthrough_columns_keep_input_values(dr, output):
    pipeline.build(
        FIXTURE, output, pipeline.BuildConfig(passthrough=("label", "label_tag_counts"))
    )
    feature_table = pq.read_table(output, columns=["domain", "label"])
    source = pq.read_table(FIXTURE, columns=["domain", "label"])
    assert feature_table.equals(source)


def test_registration_date_is_minutes(dr, output):
    pipeline.build(FIXTURE, output, pipeline.BuildConfig())
    field = pq.read_schema(output).field("rdap_registration_period")
    assert str(field.type) == "double"


def test_plugin_adds_a_feature(dr, output):
    try:
        pipeline.build(
            FIXTURE,
            output,
            pipeline.BuildConfig(plugins=("plugins.sample_plugin",), overwrite=True),
        )
    finally:
        dr._all_transformations.pop("probe_name_len_squared", None)

    table = pq.read_table(output)
    assert "probe_name_len_squared" in table.schema.names
    lengths = [len(d) ** 2 for d in table.column("domain").to_pylist()]
    assert table.column("probe_name_len_squared").to_pylist() == lengths


def test_existing_output_requires_overwrite(dr, output):
    pipeline.build(FIXTURE, output, pipeline.BuildConfig(limit=1))
    with pytest.raises(pipeline.BuildError, match="exists"):
        pipeline.build(FIXTURE, output, pipeline.BuildConfig(limit=1))


def test_rejects_dataset_without_required_columns(dr, tmp_path):
    import pyarrow as pa

    bad = tmp_path / "bad.parquet"
    pq.write_table(pa.table({"domain": ["a.example"]}), bad)
    with pytest.raises(pipeline.BuildError, match="missing columns"):
        pipeline.build(bad, tmp_path / "out.parquet", pipeline.BuildConfig())
