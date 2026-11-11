#!/usr/bin/env bash
# Rebuild tickets/<id>/{gold.patch,tests/files,tests/run.sh} from a stack_reverts.py
# output directory (drafts/docs/REAL_SEQUENCE_GUIDE.md, step 6). ticket.md is hand-written
# and left alone.
#
#   bash drafts/sequences/duckdb-2026/source/assemble.sh <stack-out-dir>
#
# The stack was made with (on the Docker host, from a clone at dce07451):
#   python3 -I stack_reverts.py --repo repo --instances instances \
#     --ids 24318,24290,24211,24181,24105,24100,24074,24011,23637,23618,23604 \
#     --fixup drafts/sequences/duckdb-2026/source/fixups/01-bloom-filters-visible-test.patch --out stack11
set -euo pipefail
stack="${1:?usage: assemble.sh <stack_reverts.py --out dir>}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
seq="$(cd "$here/.." && pwd)"
tools="$(cd "$seq/../../../scripts/real_sequence" && pwd)"

# P2P tests every ticket runs: quick, broad, and touched by no ticket. Every
# P2P test below passed both at the base and with every gold patch applied
# (README.md, interaction experiment).
CORE="test/sql/function/generic/case_condition.test,test/optimizer/constant_folding.test,test/sql/attach/attach_schema.test,test/sql/copy/parquet/parquet_count_star.test"
OPT="test/optimizer/pushdown/table_filter_pushdown.test,test/sql/optimizer/plan/test_table_filter_pushdown.test,test/optimizer/pushdown/table_or_pushdown.test"
STATS="test/optimizer/statistics/statistics_numeric.test,test/optimizer/statistics/statistics_filter_pruning.test,test/optimizer/statistics/statistics_try_cast.test,test/optimizer/statistics/statistics_unsigned_cast.test"
PRUNE="test/sql/optimizer/boolean_row_group_pruning.test,test/sql/optimizer/sign_row_group_pruning.test,test/sql/optimizer/power_zonemap_pruning.test,test/sql/optimizer/test_glob_row_group_pruning.test"

assemble() {
    # assemble <ticket> <instance number> <F2P,...> <area P2P,...>
    local t="$1" i="$2" f2p="$3" p2p="$4" d="$seq/tickets/$1"
    rm -rf "$d/tests/files" "$d/gold.patch"
    mkdir -p "$d/tests/files"
    cp "$stack/gold/duckdb__duckdb-$i.patch" "$d/gold.patch"
    cp -a "$stack/hidden/duckdb__duckdb-$i/." "$d/tests/files/"
    # The test configs the base changed only matter to the CI config runs, not
    # to the plain unittest run the grader does; keep the trusted copies.
    rm -rf "$d/tests/files/test/configs"
    # Listed as F2P upstream for one instance but unchanged by it and passing at
    # the base (interaction experiment): not this ticket's test.
    rm -f "$d/tests/files/test/sql/settings/enable_caching_operators.test_slow"
    python3 -I "$tools/render_run_sh.py" --template "$tools/templates/run_duckdb_sqllogic.sh" \
        --sequence duckdb-2026 --ticket "$t" --f2p "$f2p" --p2p "$CORE,$p2p" --out "$d/tests/run.sh"
}

assemble DDB-01 24011 test/sql/copy/parquet/bloom_filters.test \
    "$OPT,test/optimizer/statistics/statistics_between.test,test/optimizer/pushdown/parquet_or_pushdown.test,test/optimizer/pushdown/pushdown_in_to_parquet.test,test/optimizer/pushdown/distinct_from_pushdown.test,test/sql/optimizer/expression/test_in_clause_simplification.test,test/sql/function/generic/test_between.test,test/optimizer/conjunction_simplification.test,test/sql/optimizer/expression/test_comparison_simplification.test,test/sql/copy/parquet/parquet_corrupt_stats.test"
assemble DDB-02 23604 test/optimizer/pushdown/starts_with_pushdown.test \
    "$OPT,test/optimizer/filter_pushdown/like_prefix_boundary.test,test/optimizer/pushdown/scalar_function_pushdown.test,test/optimizer/pushdown/filter_cannot_pushdown.test,test/optimizer/regex_to_like_optimizer.test,test/optimizer/statistics/statistics_varchar.test,test/optimizer/statistics/statistics_suffix.test,test/optimizer/statistics/statistics_contains.test"
assemble DDB-03 24290 test/sql/optimizer/array_extract_row_group_pruning.test \
    "$STATS,$PRUNE,test/optimizer/statistics/statistics_struct.test,test/sql/optimizer/plan/plan_struct_projection_pushdown.test,test/optimizer/pushdown/pushdown_after_statistics.test"
assemble DDB-04 24074 test/sql/copy/parquet/parquet_stats.test \
    "test/optimizer/statistics/statistics_aggregate.test,test/optimizer/statistics/statistics_try_cast.test,test/optimizer/statistics/statistics_unsigned_cast.test,test/optimizer/partition_stats_prepared.test,test/optimizer/misc/test_count_and_sum.test,test/sql/copy/parquet/parquet_corrupt_stats.test,test/sql/copy/parquet/corrupt_stats.test,test/sql/copy/parquet/copy_ninf_stats.test,test/sql/copy/parquet/invalid_utf8_stats.test,test/sql/optimizer/expression/test_indistinct_aggregates.test,test/sql/optimizer/any_extremum_rewrite.test"
assemble DDB-05 23618 test/optimizer/statistics/statistics_least_greatest.test \
    "test/sql/function/generic/test_least_greatest.test,test/sql/function/generic/least_greatest_types.test,test/sql/function/generic/least_greatest_enum.test,test/optimizer/statistics/statistics_case.test,test/optimizer/statistics/statistics_coalesce.test,test/optimizer/statistics/statistics_numeric.test,test/optimizer/statistics/statistics_null_comparison.test,test/optimizer/statistics/statistics_is_null.test,test/optimizer/statistics/statistics_filter_multicolumn.test,test/optimizer/statistics/statistics_string_length.test"
assemble DDB-06 24100 test/sql/optimizer/test_rowid_pruning.test \
    "$PRUNE,test/sql/optimizer/test_rowid_pushdown.test,test/optimizer/statistics/statistics_filter_pruning.test,test/optimizer/statistics/statistics_between.test,test/sql/storage/update/test_store_updates.test,test/sql/optimizer/plan/test_table_filter_pushdown.test"
assemble DDB-07 24181 test/sql/storage/wal/wal_null_update_row_group_pruning.test \
    "test/sql/storage/update/dictionary_update_null.test,test/sql/storage/update/test_store_null_updates.test,test/sql/storage/update/test_store_updates.test,test/sql/storage/update/wal_restart_update_insert.test,test/sql/storage/wal/wal_test_null_updates.test,test/sql/storage/wal/wal_test_string_null_updates.test,test/sql/storage/wal/wal_test_updates.test,test/sql/storage/wal/wal_store_mixed_updates_big_null.test,test/sql/storage/wal/wal_store_updates_big.test,test/optimizer/statistics/statistics_is_null.test,test/optimizer/statistics/statistics_null_comparison.test"
assemble DDB-08 24105 test/sql/storage/test_read_only_wal_replay.test \
    "test/sql/storage/wal/wal_index_replay.test,test/sql/attach/attach_read_only.test,test/sql/attach/attach_read_only_transaction.test,test/sql/attach/attach_create_index.test,test/sql/attach/attach_replay_with_no_wal_writes.test,test/sql/attach/attach_wal_alter.test,test/sql/index/art/storage/test_art_readonly.test,test/sql/index/art/create_drop/test_art_create_unique.test,test/sql/index/art/insert_update_delete/test_art_simple_update.test,test/sql/index/art/scan/test_art_scan_thresholds.test,test/sql/storage/wal/wal_store_add_column.test,test/sql/alter/add_pk/test_add_pk_wal.test"
assemble DDB-09 24318 "test/sql/copy/parquet/parquet_hive_null.test,test/sql/copy/parquet/parquet_hive_empty.test" \
    "test/sql/copy/parquet/parquet_hive.test,test/sql/copy/parquet/parquet_hive2.test,test/sql/copy/parquet/hive_timestamps.test,test/sql/copy/parquet/hive_partitioning_struct.test,test/sql/copy/parquet/union_by_name_hive_partitioning.test,test/sql/copy/partitioned/hive_filter_pushdown.test,test/sql/copy/partitioned/hive_partition_append.test,test/sql/copy/partitioned/hive_partition_case_insensitive_column.test,test/sql/copy/partitioned/hive_partition_escape.test,test/sql/copy/partitioned/hive_partitioned_write.test,test/sql/copy/partitioned/hive_partitioning_overwrite.test"
assemble DDB-10 24211 "test/sql/alter/struct/rename_col_struct.test,test/sql/alter/struct/rename_col_nested_struct.test" \
    "test/sql/alter/struct/rename_col_struct_case_insensitive.test,test/sql/alter/struct/add_col_struct.test,test/sql/alter/struct/add_col_nested_struct.test,test/sql/alter/struct/drop_col_struct.test,test/sql/alter/struct/drop_col_nested_struct.test,test/sql/alter/list/rename_column_in_struct.test,test/sql/alter/map/rename_column_in_struct.test,test/sql/alter/alter_type/alter_type_struct.test,test/sql/alter/default/test_set_default.test,test/sql/alter/rename_col/test_rename_col.test,test/sql/storage/wal/wal_store_defaults.test,test/sql/storage/wal/wal_store_rename_column.test"
assemble DDB-11 23637 test/sql/window/test_empty_frames.test \
    "test/sql/window/test_value_exclude.test,test/sql/window/test_lead_lag.test,test/sql/window/test_streaming_lead_lag.test,test/sql/window/test_rank.test,test/sql/window/test_window_filter.test,test/sql/window/test_boundary_expr.test,test/sql/window/test_negative_range.test,test/sql/window/test_nthvalue.test,test/sql/window/test_evil_window.test,test/sql/window/test_window_constant_allocator.test,test/sql/window/test_invalid_window.test,test/sql/window/test_window_clause.test"
