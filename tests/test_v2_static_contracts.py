from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


class V2StaticContractsTest(unittest.TestCase):
    def test_standalone_repo_root_resolution(self) -> None:
        import v2_common

        self.assertEqual(v2_common.V2_ROOT, ROOT)
        self.assertTrue(str(v2_common.V1_ROOT).endswith(str(Path("Breaker_Based") / "signal_model")))

    def test_required_v2_files_exist(self) -> None:
        required = [
            ROOT / "README.md",
            ROOT / "configs" / "v2_runtime_config.example.json",
            ROOT / "configs" / "v2_model_registry.json",
            ROOT / "configs" / "v2_feature_source_contract.json",
            ROOT / "docs" / "V2_ARCHITECTURE.md",
            ROOT / "scripts" / "v2_run_bridge_smoke.py",
            ROOT / "scripts" / "v2_audit_system.py",
            ROOT / "scripts" / "v2_ingest_candles.py",
            ROOT / "scripts" / "v2_update_candle_store_incremental.py",
            ROOT / "scripts" / "v2_import_payload_candles.py",
            ROOT / "scripts" / "v2_detect_signals_from_candles.py",
            ROOT / "scripts" / "v2_build_live_features_from_events.py",
            ROOT / "scripts" / "v2_build_liquidity_payloads_from_events.py",
            ROOT / "scripts" / "v2_score_liquidity_candidates.py",
            ROOT / "scripts" / "v2_feature_broad_validation_audit.py",
            ROOT / "scripts" / "v2_apply_signal_decision_gate.py",
            ROOT / "scripts" / "v2_run_signal_inference.py",
            ROOT / "scripts" / "v2_run_replay_smoke_pipeline.py",
            ROOT / "scripts" / "v2_run_burnin_cycle.py",
            ROOT / "scripts" / "v2_run_runtime_cycle.py",
            ROOT / "scripts" / "v2_run_live_paper_cycle.py",
            ROOT / "scripts" / "v2_paper_replay_from_decisions.py",
            ROOT / "scripts" / "v2_export_dashboard_bridge.py",
            ROOT / "scripts" / "v2_aws_readiness_audit.py",
            ROOT / "scripts" / "v2_production_readiness_audit.py",
            ROOT / "scripts" / "v2_goal_completion_audit.py",
            ROOT / "scripts" / "v2_validation_gate_audit.py",
        ]
        missing = [str(path) for path in required if not path.exists()]
        self.assertFalse(missing)

    def test_feature_contract_forbids_outcome_columns(self) -> None:
        contract = json.loads((ROOT / "configs" / "v2_feature_source_contract.json").read_text(encoding="utf-8"))
        forbidden = set(contract["label_columns_forbidden_as_features"])
        self.assertIn("hit_at_least_2_bsl", forbidden)
        self.assertIn("hit_at_least_2_ssl", forbidden)
        self.assertGreaterEqual(contract["feature_time_rule"].lower().find("decision_time"), 0)

    def test_candle_ingestion_normalizes_and_deduplicates(self) -> None:
        from v2_ingest_candles import audit_normalized, normalize_frame

        raw = pd.DataFrame(
            [
                {"time": 1742550300, "open": 100, "high": 110, "low": 95, "close": 105},
                {"time": 1742553900, "open": 105, "high": 112, "low": 102, "close": 108},
                {"time": 1742553900, "open": 106, "high": 113, "low": 103, "close": 109},
                {"time": "bad", "open": 109, "high": 115, "low": 108, "close": 114},
                {"time": 1742557500, "open": 114, "high": 100, "low": 116, "close": 112},
            ]
        )
        frame, normalization = normalize_frame(raw, "Asia/Calcutta")
        quality = audit_normalized(frame, min_rows=2, interval="1h")

        self.assertEqual(len(frame), 2)
        self.assertEqual(normalization["duplicate_time_rows_removed"], 1)
        self.assertEqual(normalization["null_or_unparseable_rows_dropped"], 1)
        self.assertEqual(normalization["invalid_ohlc_rows_dropped"], 1)
        self.assertTrue(quality["passed"])
        self.assertEqual(frame.iloc[1]["close"], 109)

    def test_candle_ingestion_accepts_timezone_aware_provider_times(self) -> None:
        from v2_ingest_candles import audit_normalized, normalize_frame

        raw = pd.DataFrame(
            [
                {"Datetime": "2026-01-01 09:15:00+05:30", "Open": 100, "High": 106, "Low": 98, "Close": 104},
                {"Datetime": "2026-01-01 10:15:00+05:30", "Open": 104, "High": 109, "Low": 103, "Close": 108},
            ]
        )
        frame, normalization = normalize_frame(raw, "Asia/Calcutta")
        quality = audit_normalized(frame, min_rows=2, interval="1h")

        self.assertEqual(len(frame), 2)
        self.assertEqual(normalization["null_or_unparseable_rows_dropped"], 0)
        self.assertTrue(quality["passed"])

    def test_candle_ingestion_accepts_yfinance_datetime_dtype(self) -> None:
        from v2_ingest_candles import audit_normalized, normalize_frame

        raw = pd.DataFrame(
            [
                {"Datetime": pd.Timestamp("2026-01-01 03:45:00", tz="UTC"), "Open": 100, "High": 106, "Low": 98, "Close": 104},
                {"Datetime": pd.Timestamp("2026-01-01 04:45:00", tz="UTC"), "Open": 104, "High": 109, "Low": 103, "Close": 108},
            ]
        )
        frame, normalization = normalize_frame(raw, "Asia/Calcutta")
        quality = audit_normalized(frame, min_rows=2, interval="1h")

        self.assertEqual(len(frame), 2)
        self.assertEqual(normalization["null_or_unparseable_rows_dropped"], 0)
        self.assertTrue(quality["passed"])

    def test_incremental_candle_store_merges_overlap_and_retains_new_rows(self) -> None:
        from v2_update_candle_store_incremental import merge_frames

        existing = pd.DataFrame(
            [
                {"time": 1000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5},
                {"time": 4600, "open": 101.0, "high": 102.0, "low": 100.0, "close": 101.5},
                {"time": 8200, "open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5},
            ]
        )
        incoming = pd.DataFrame(
            [
                {"time": 8200, "open": 202.0, "high": 203.0, "low": 201.0, "close": 202.5},
                {"time": 11800, "open": 103.0, "high": 104.0, "low": 102.0, "close": 103.5},
            ]
        )

        merged, audit = merge_frames(existing, incoming, max_store_candles=10)

        self.assertEqual(len(merged), 4)
        self.assertEqual(audit["duplicate_time_rows_removed"], 1)
        self.assertEqual(audit["new_timestamps_seen"], 1)
        self.assertEqual(audit["new_timestamps_retained"], 1)
        self.assertEqual(float(merged.loc[merged["time"] == 8200, "open"].iloc[0]), 202.0)

    def test_incremental_candle_store_can_trim_without_losing_latest_rows(self) -> None:
        from v2_update_candle_store_incremental import merge_frames

        existing = pd.DataFrame(
            {"time": [1000, 4600, 8200], "open": [1, 2, 3], "high": [2, 3, 4], "low": [0, 1, 2], "close": [1.5, 2.5, 3.5]}
        )
        incoming = pd.DataFrame(
            {"time": [11800, 15400], "open": [4, 5], "high": [5, 6], "low": [3, 4], "close": [4.5, 5.5]}
        )

        merged, audit = merge_frames(existing, incoming, max_store_candles=3)

        self.assertEqual(merged["time"].tolist(), [8200, 11800, 15400])
        self.assertEqual(audit["trimmed_rows"], 2)
        self.assertEqual(audit["new_timestamps_retained"], 2)

    def test_short_detector_mirror_transform_preserves_ohlc_shape(self) -> None:
        from v2_detect_signals_from_candles import mirror_candles_for_short

        raw = pd.DataFrame(
            [
                {"Open": 100.0, "High": 110.0, "Low": 95.0, "Close": 105.0},
                {"Open": 105.0, "High": 108.0, "Low": 99.0, "Close": 101.0},
            ]
        )
        mirrored = mirror_candles_for_short(raw)

        self.assertEqual(mirrored.iloc[0]["Open"], -100.0)
        self.assertEqual(mirrored.iloc[0]["High"], -95.0)
        self.assertEqual(mirrored.iloc[0]["Low"], -110.0)
        self.assertEqual(mirrored.iloc[0]["Close"], -105.0)
        self.assertTrue((mirrored["High"] >= mirrored["Low"]).all())

    def test_detector_window_keeps_latest_candles_and_audits_raw_span(self) -> None:
        from v2_detect_signals_from_candles import apply_detector_window

        raw = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
                "High": [101.0, 102.0, 103.0, 104.0, 105.0],
                "Low": [99.0, 100.0, 101.0, 102.0, 103.0],
                "Close": [100.5, 101.5, 102.5, 103.5, 104.5],
            },
            index=pd.to_datetime([1770000000, 1770003600, 1770007200, 1770010800, 1770014400], unit="s", utc=True),
        )

        window, audit = apply_detector_window(raw, 3)

        self.assertEqual(len(window), 3)
        self.assertEqual(window.iloc[0]["Open"], 102.0)
        self.assertTrue(audit["windowing_applied"])
        self.assertEqual(audit["raw_input_rows"], 5)
        self.assertEqual(audit["detector_input_rows"], 3)
        self.assertEqual(audit["raw_first_time"], 1770000000)
        self.assertEqual(audit["detector_first_time"], 1770007200)

    def test_short_event_mapping_keeps_stop_above_entry(self) -> None:
        from v2_detect_signals_from_candles import to_event_row

        signal = {
            "time": 1770000000,
            "timestamp": "2026-02-01 10:15",
            "price": -100.0,
            "score": 70.0,
            "levels": {
                "T3 Low": -115.0,
                "T2 High": -96.0,
                "T1 Sweep Low": -110.0,
                "Signal High": -100.0,
                "Current ISL": -109.0,
                "Base ISL": -111.0,
                "Base ISH": -98.0,
                "Deeper ISL": -113.0,
                "Bull FVG Lower": -106.0,
                "Bull FVG Upper": -104.0,
            },
            "level_times": {"T1 Sweep Low": 1769996400, "Bull FVG": 1769998200},
            "metrics": {},
        }
        row = to_event_row("TEST.NS", "short", signal, Path("candles.csv"), "signal_time")

        self.assertEqual(row["direction"], "short")
        self.assertEqual(row["entry_price"], 100.0)
        self.assertEqual(row["stop_price"], 110.0)
        self.assertEqual(row["risk"], 10.0)
        self.assertEqual(row["target_1r"], 90.0)
        self.assertEqual(row["target_2r"], 80.0)
        self.assertEqual(row["t1_sweep_high_price"], 110.0)
        self.assertEqual(row["bear_fvg_lower_price"], 104.0)
        self.assertEqual(row["bear_fvg_upper_price"], 106.0)

    def test_short_feature_builder_applies_compatibility_aliases_without_scoring_approval(self) -> None:
        from v2_build_live_features_from_events import base_event_features

        row = base_event_features(
            {
                "side": "short",
                "entry_price": 100.0,
                "t3_high_price": 120.0,
                "t2_low_price": 96.0,
                "t1_sweep_high_price": 112.0,
                "signal_low_price": 100.0,
                "bear_fvg_lower_price": 104.0,
                "bear_fvg_upper_price": 106.0,
            },
            {},
        )

        self.assertEqual(row["v2_short_compatibility_aliases_applied"], 1)
        self.assertEqual(row["t1_sweep_low_price"], 112.0)
        self.assertEqual(row["signal_high_price"], 100.0)
        self.assertEqual(row["bull_fvg_lower"], 104.0)
        self.assertEqual(row["bull_fvg_upper"], 106.0)
        self.assertIsNotNone(row["sweep_to_signal_return"])

    def test_long_feature_builder_uses_stop_as_protected_low_fallback(self) -> None:
        from v2_build_live_features_from_events import base_event_features

        row = base_event_features(
            {
                "side": "long",
                "entry_price": 100.0,
                "stop_price": 94.0,
                "t1_sweep_low_price": 95.0,
                "signal_high_price": 102.0,
            },
            {},
        )

        self.assertEqual(row["current_isl_price"], 95.0)
        self.assertEqual(row["base_isl_price"], 95.0)
        self.assertEqual(row["isl_level"], 95.0)

    def test_feature_safe_div_treats_blank_placeholders_as_missing(self) -> None:
        from v2_build_live_features_from_events import safe_div

        self.assertIsNone(safe_div("", 2.0))
        self.assertIsNone(safe_div(1.0, ""))
        self.assertEqual(safe_div("6", "3"), 2.0)

    def test_payload_fvg_fallback_fills_long_named_fvg_fields(self) -> None:
        from v2_build_live_features_from_events import apply_payload_fvg_fallbacks

        row = {
            "side": "long",
            "signal_price": 106.0,
            "t1_sweep_low_price": 101.0,
            "signal_high_price": 108.0,
            "t2_high_price": 110.0,
            "tech_atr20": 5.0,
            "fq_reversal_speed_atr_per_bar": 0.5,
            "active_bull_fvgs": 1,
            "tech_nearest_bull_fvg_lower": 100.0,
            "tech_nearest_bull_fvg_upper": 105.0,
            "tech_nearest_bull_fvg_fill_pct_at_decision": 0.2,
            "tech_nearest_bull_fvg_age_bars_at_decision": 7,
        }
        apply_payload_fvg_fallbacks(row)

        self.assertEqual(row["v2_payload_fvg_fallback_applied"], 1)
        self.assertEqual(row["bull_fvg_lower"], 100.0)
        self.assertEqual(row["bull_fvg_upper"], 105.0)
        self.assertEqual(row["bull_fvg_fill"], 0.2)
        self.assertEqual(row["bull_fvg_age"], 7)
        self.assertEqual(row["fvg_zone_size"], 5.0)
        self.assertEqual(row["fq_bull_fvg_zone_size_atr"], 1.0)
        self.assertEqual(row["fvg_react_has_bull_fvg"], 1)

    def test_focused_quality_zero_fills_no_target_ratio_features(self) -> None:
        from v2_build_live_features_from_events import add_focused_quality_features

        frame = pd.DataFrame(
            [
                {
                    "side": "long",
                    "signal_price": 100.0,
                    "tech_atr20": 5.0,
                    "xg_liq_target_side_count": 0,
                    "xg_liq_target_side_pressure": 0,
                    "dt_liq_target_path_pool_count": 0,
                    "dt_liq_target_path_pressure": 0,
                    "dt_liq_target_path_proxy_score_sum": 0,
                }
            ]
        )

        add_focused_quality_features(frame)

        for column in [
            "fq_reversal_max_fvg_per_range",
            "fq_xg_target_score_per_distance",
            "fq_xg_target_pressure_per_distance",
            "fq_dt_target_pressure_per_pool",
            "fq_dt_target_stack_score_per_distance",
            "fq_topology_score_distance_edge",
        ]:
            self.assertEqual(frame.iloc[0][column], 0)

    def test_liquidity_payload_builder_bounds_decision_time_lookback(self) -> None:
        from v2_build_liquidity_payloads_from_events import build_payload_for_event

        class FakeEngine:
            def __init__(self) -> None:
                self.seen_rows = 0

            def analyze_ticker(self, ticker: str, frame: pd.DataFrame, _flag: bool) -> dict:
                self.seen_rows = len(frame)
                return {"ticker": ticker, "rows_seen": len(frame), "signals": []}

        candles = pd.DataFrame(
            {
                "time": list(range(1770000000, 1770000000 + 100 * 3600, 3600)),
                "open": [100.0] * 100,
                "high": [101.0] * 100,
                "low": [99.0] * 100,
                "close": [100.5] * 100,
            }
        )
        event = {
            "ticker": "TEST.NS",
            "signal_id": "TEST.NS|long|unit",
            "decision_time": candles.iloc[-1]["time"],
            "signal_time": candles.iloc[-2]["time"],
            "side": "long",
        }
        engine = FakeEngine()

        with tempfile.TemporaryDirectory() as tmp:
            result = build_payload_for_event(engine, event, candles, Path(tmp), max_input_candles=60)

        self.assertEqual(result["status"], "written")
        self.assertEqual(result["raw_cutoff_candle_count"], 100)
        self.assertEqual(result["candle_count"], 60)
        self.assertTrue(result["lookback_window_applied"])
        self.assertEqual(engine.seen_rows, 60)

    def test_feature_audit_uses_short_artifact_contract_for_short_rows(self) -> None:
        from v2_build_live_features_from_events import build_audit

        rows = [
            {"signal_id": "TEST.NS|long|1|abc", "side": "long", "a": 1},
            {"signal_id": "TEST.NS|short|2|abc", "side": "short", "a": 1},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audit = build_audit(
                "unit_short_contract",
                rows,
                ["a"],
                ["a", "short_only_required"],
                tmp_path / "events.csv",
                tmp_path / "candles.csv",
                tmp_path / "features.csv",
                tmp_path / "summary.csv",
                tmp_path / "log.jsonl",
                None,
                None,
                0,
                None,
                "none",
                0,
                None,
                0,
                {"loaded": False, "version": "unit", "structural_nullable_patterns": []},
                {
                    "long": {"approved_inference_contract": True, "status": "approved"},
                    "short": {"approved_inference_contract": True, "status": "approved"},
                },
            )

        self.assertEqual(audit["long_required_feature_count"], 1)
        self.assertEqual(audit["short_required_feature_count"], 2)
        self.assertEqual(audit["side_feature_status"]["long"]["blocking_missing_all_rows_count"], 0)
        self.assertEqual(audit["side_feature_status"]["short"]["required_feature_count"], 2)
        self.assertEqual(audit["side_feature_status"]["short"]["blocking_missing_all_rows_count"], 1)
        self.assertIn("short_only_required", audit["side_feature_status"]["short"]["blocking_missing_features_sample"])

    def test_signal_inference_blocks_unaudited_short_rows(self) -> None:
        from v2_run_signal_inference import side_score_plan

        rows = [
            {"signal_id": "TEST.NS|long|1|abc", "side": "long"},
            {"signal_id": "TEST.NS|short|2|abc", "side": "short"},
        ]
        audit = {
            "classification_allowed": True,
            "classification_status": "ready",
            "missing_all_rows_count": 0,
            "blocking_missing_all_rows_count": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.json"
            audit_path.write_text(json.dumps(audit), encoding="utf-8")
            allowed, blocked, plan = side_score_plan(rows, audit_path)

        self.assertEqual(len(allowed), 1)
        self.assertEqual(allowed[0]["side"], "long")
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["side"], "short")
        self.assertIn("short_feature_contract_not_audited", blocked[0]["_v2_block_reason"])
        self.assertEqual(plan["allowed_rows"], 1)
        self.assertEqual(plan["blocked_rows"], 1)

    def test_dashboard_bridge_uses_short_prediction_as_model_score(self) -> None:
        from v2_export_dashboard_bridge import dashboard_contract_summary, make_dashboard_row

        row = {
            "signal_id": "TEST.NS|short|1|abc",
            "ticker": "TEST.NS",
            "side": "short",
            "direction": "short",
            "decision_time": "1779248700",
            "prediction": "0.42",
            "tds_decision_class": "neutral_no_edge",
            "tds_entry_permission": "skip",
            "entry_price": "100",
            "stop_price": "110",
            "risk": "10",
            "tds_reason": "unit reason",
        }
        liquidity = {
            "TEST.NS|short|1|abc": [
                {
                    "candidate_role": "target_side",
                    "candidate_side": "SSL",
                    "midpoint": "90",
                    "candidate_distance_to_signal_atr": "1.5",
                    "approved_model_score": "61.5",
                }
            ]
        }
        bridge_row = make_dashboard_row(row, liquidity)
        contract = dashboard_contract_summary([bridge_row])

        self.assertEqual(bridge_row["model_score"], 0.42)
        self.assertEqual(bridge_row["scored_liquidity_context"][0]["side"], "SSL")
        self.assertTrue(contract["contract_ok"])

    def test_dashboard_bridge_separates_approved_and_mixed_rank_layers(self) -> None:
        from v2_export_dashboard_bridge import assign_mixed_rank_fields, dashboard_contract_summary, make_dashboard_row

        rows = [
            make_dashboard_row(
                {
                    "signal_id": "TEST.NS|long|1|abc",
                    "ticker": "TEST.NS",
                    "side": "long",
                    "direction": "long",
                    "decision_time": "1779248700",
                    "score": "0.0",
                    "raw_model_score": "0.80",
                    "tds_decision_class": "reject",
                    "tds_entry_permission": "reject",
                    "entry_price": "100",
                    "stop_price": "95",
                    "risk": "5",
                    "tds_reason": "unit reject",
                    "main_gate_pass": "false",
                    "main_gate_failures": "unit gate failure",
                    "dt_target_liquidity_count": "1",
                },
                {},
            ),
            make_dashboard_row(
                {
                    "signal_id": "TEST.NS|long|2|def",
                    "ticker": "TEST.NS",
                    "side": "long",
                    "direction": "long",
                    "decision_time": "1779252300",
                    "score": "0.70",
                    "raw_model_score": "0.70",
                    "strict_score": "0.70",
                    "tds_decision_class": "ultra_high_conviction",
                    "tds_entry_permission": "take_candidate",
                    "entry_price": "100",
                    "stop_price": "95",
                    "risk": "5",
                    "tds_reason": "unit take",
                    "main_gate_pass": "true",
                    "strict_gate_pass": "true",
                    "dt_target_liquidity_count": "1",
                },
                {},
            ),
        ]
        assign_mixed_rank_fields(rows)
        contract = dashboard_contract_summary(rows)

        self.assertEqual(rows[0]["approved_trade_bucket"], "reject")
        self.assertEqual(rows[0]["approved_entry_permission"], "no")
        self.assertEqual(rows[0]["approved_raw_score"], 0.80)
        self.assertEqual(rows[0]["approved_final_score"], 0.0)
        self.assertEqual(rows[0]["mixed_rank_bucket"], "mixed_top20")
        self.assertEqual(rows[0]["mixed_rank_lineage"], "mixed-train, original-current")
        self.assertEqual(rows[1]["approved_trade_bucket"], "ultra_high_conviction")
        self.assertTrue(contract["contract_ok"])

    def test_paper_replay_normalizes_entry_permission_values(self) -> None:
        from v2_paper_replay_from_decisions import normalize_permission_value, permission_from_row

        self.assertEqual(normalize_permission_value("yes"), "yes")
        self.assertEqual(normalize_permission_value("take_trade"), "yes")
        self.assertEqual(normalize_permission_value("conditional_take_candidate"), "conditional_take_candidate")
        self.assertEqual(normalize_permission_value("skip_no_trade"), "no")
        self.assertEqual(
            permission_from_row({"permission": "review", "tds_entry_permission": "conditional_take_candidate"}),
            "conditional_take_candidate",
        )

    def test_dashboard_bridge_accepts_insufficient_data_reason_codes(self) -> None:
        from v2_export_dashboard_bridge import dashboard_contract_summary, make_dashboard_row

        row = {
            "signal_id": "TEST.NS|long|1|abc",
            "ticker": "TEST.NS",
            "side": "long",
            "direction": "long",
            "decision_time": "1779248700",
            "bucket": "insufficient_data",
            "permission": "no",
            "entry_price": "100",
            "stop_price": "95",
            "risk": "5",
            "reason_codes": "missing required features",
            "missing_required_feature_count": "3",
        }
        bridge_row = make_dashboard_row(row, {})
        contract = dashboard_contract_summary([bridge_row])

        self.assertEqual(bridge_row["bucket"], "insufficient_data")
        self.assertEqual(bridge_row["reason"], "missing required features")
        self.assertEqual(bridge_row["missing_fields"], "missing_required_feature_count=3")
        self.assertTrue(contract["contract_ok"])

    def test_dashboard_bridge_accepts_summary_only_zero_liquidity_context(self) -> None:
        from v2_export_dashboard_bridge import dashboard_contract_summary, make_dashboard_row

        row = {
            "signal_id": "TEST.NS|long|1|abc",
            "ticker": "TEST.NS",
            "side": "long",
            "direction": "long",
            "decision_time": "1779248700",
            "bucket": "reject",
            "permission": "no",
            "prediction": "0.01",
            "entry_price": "100",
            "stop_price": "95",
            "risk": "5",
            "reason_codes": "no visible target-side liquidity",
            "dt_target_liquidity_count": "0",
            "dt_adverse_liquidity_count": "0",
            "xg_liq_target_minus_stop_pressure": "0",
        }

        bridge_row = make_dashboard_row(row, {})
        contract = dashboard_contract_summary([bridge_row])

        self.assertEqual(bridge_row["liquidity_context_status"], "summary_only_no_visible_levels")
        self.assertEqual(bridge_row["summary_metrics"]["target_liquidity_count"], 0.0)
        self.assertTrue(contract["contract_ok"])

    def test_replay_batch_resume_reconstructs_ticker_rows(self) -> None:
        from v2_run_replay_batch import row_from_ticker_audit

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            passed_audit = tmp_path / "passed_audit.json"
            passed_report = tmp_path / "passed_report.md"
            passed_audit.write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "summary": {
                            "signal_rows": 2,
                            "liquidity_candidate_rows": 4,
                            "scored_liquidity_rows": 4,
                            "aggregated_signal_rows": 2,
                            "feature_rows": 2,
                            "decision_rows": 2,
                            "classification_allowed": True,
                            "signal_inference_path": "approved_artifact_scoring",
                            "decision_bucket_counts": {"reject": 2},
                        },
                    }
                ),
                encoding="utf-8",
            )
            row = row_from_ticker_audit(
                "TEST.NS",
                tmp_path / "TEST.NS_1h.csv",
                "unit_run_test",
                passed_audit,
                passed_report,
                resumed=True,
            )

            self.assertTrue(row["resumed"])
            self.assertEqual(row["status"], "passed")
            self.assertEqual(row["decision_rows"], 2)
            self.assertEqual(row["signal_inference_path"], "approved_artifact_scoring")

            no_signal_audit = tmp_path / "no_signal_audit.json"
            no_signal_audit.write_text(
                json.dumps({"status": "failed", "error": "Signal detection produced zero rows; pipeline cannot continue."}),
                encoding="utf-8",
            )
            no_signal_row = row_from_ticker_audit(
                "EMPTY.NS",
                tmp_path / "EMPTY.NS_1h.csv",
                "unit_run_empty",
                no_signal_audit,
                tmp_path / "no_signal_report.md",
                resumed=True,
            )

            self.assertEqual(no_signal_row["status"], "no_signals")
            self.assertEqual(no_signal_row["decision_rows"], 0)
            self.assertIn("zero rows", no_signal_row["error"])

    def test_runtime_cycle_uses_configured_rolling_lookback_when_cli_unset(self) -> None:
        from v2_run_runtime_cycle import resolve_effective_max_input_candles

        value, source = resolve_effective_max_input_candles(
            None,
            {"rolling_history": {"runtime_lookback_candles": 1500}},
        )

        self.assertEqual(value, 1500)
        self.assertEqual(source, "runtime_config")

        cli_value, cli_source = resolve_effective_max_input_candles(
            2000,
            {"rolling_history": {"runtime_lookback_candles": 1500}},
        )

        self.assertEqual(cli_value, 2000)
        self.assertEqual(cli_source, "cli")

    def test_feature_broad_validation_uses_ticker_run_feature_audits(self) -> None:
        from v2_feature_broad_validation_audit import feature_audit_path_for_ticker_run

        path = feature_audit_path_for_ticker_run("unit_replay_test_ns")

        self.assertEqual(path.name, "unit_replay_test_ns_features_audit.json")
        self.assertEqual(path.parent.name, "audits")

    def test_empty_liquidity_group_uses_zero_aggregates_without_fake_nearest(self) -> None:
        from v2_build_live_features_from_events import group_score_stats

        stats = group_score_stats([])

        self.assertEqual(stats["count"], 0.0)
        self.assertEqual(stats["score_sum"], 0.0)
        self.assertEqual(stats["score_mean"], 0.0)
        self.assertEqual(stats["score_max"], 0.0)
        self.assertEqual(stats["pressure"], 0.0)
        self.assertIsNone(stats["nearest_distance"])

    def test_long_liquidity_aliases_zero_fill_no_stop_side_without_fake_nearest(self) -> None:
        from v2_build_live_features_from_events import add_long_topology_aliases_from_liquidity

        row = {
            "side": "long",
            "entry_price": 100.0,
            "dt_target_liquidity_count": 2,
            "dt_target_liquidity_score_sum": 120.0,
            "dt_target_liquidity_score_mean": 60.0,
            "dt_target_liquidity_score_max": 62.0,
            "dt_target_liquidity_pressure": 30.0,
            "dt_target_liquidity_nearest_distance_atr": 1.5,
            "dt_target_liquidity_nearest_score": 62.0,
            "dt_adverse_liquidity_count": 0,
        }

        add_long_topology_aliases_from_liquidity(row)

        self.assertEqual(row["topo_ssl_below_score_sum"], 0.0)
        self.assertEqual(row["topo_ssl_below_score_mean"], 0.0)
        self.assertEqual(row["topo_ssl_below_score_max"], 0.0)
        self.assertEqual(row["topo_downside_score_per_pool"], 0.0)
        self.assertEqual(row["xg_liq_stop_or_swept_side_score_sum"], 0.0)
        self.assertIsNone(row["topo_nearest_ssl_below_distance_atr"])
        self.assertIsNone(row["topo_nearest_ssl_below_score"])
        self.assertEqual(row["target_distance_atr"], 1.5)
        self.assertEqual(row["metric_target_distance_atr"], 1.5)
        self.assertEqual(row["tech_engine_target_distance_atr"], 1.5)

    def test_scored_candidate_topology_exports_side_aware_xg_aggregates(self) -> None:
        from v2_build_live_features_from_events import add_candidate_topology_features

        row = {"side": "long", "entry_price": 100.0, "signal_price": 100.0, "dt_liq_atr20_at_decision": 10.0}
        candidates = [
            {"candidate_side": "BSL", "midpoint": "115", "approved_model_score": "70"},
            {"candidate_side": "BSL", "midpoint": "130", "approved_model_score": "60"},
        ]

        add_candidate_topology_features(row, candidates)

        self.assertEqual(row["xg_liq_target_side_count"], 2.0)
        self.assertEqual(row["xg_liq_target_side_score_sum"], 130.0)
        self.assertEqual(row["xg_liq_stop_or_swept_side_count"], 0.0)
        self.assertEqual(row["xg_liq_stop_or_swept_side_score_sum"], 0.0)
        self.assertIsNone(row["xg_liq_stop_or_swept_side_nearest_score"])
        self.assertEqual(row["target_distance_atr"], 1.5)

    def test_feature_policy_marks_absent_structural_levels_nullable(self) -> None:
        from v2_build_live_features_from_events import is_structural_nullable, load_feature_availability_policy

        policy = load_feature_availability_policy(ROOT / "configs" / "v2_feature_availability_policy.json")

        self.assertTrue(is_structural_nullable("base_ish_price", policy))
        self.assertTrue(is_structural_nullable("topo_nearest_ssl_below_distance_atr", policy))
        self.assertTrue(is_structural_nullable("xg_liq_stop_or_swept_side_nearest_score", policy))
        self.assertTrue(is_structural_nullable("topo_upside_to_downside_pressure_ratio", policy))
        self.assertTrue(is_structural_nullable("deeper_isl_price", policy))
        self.assertTrue(is_structural_nullable("tech_engine_nearest_deeper_isl_distance_atr", policy))
        self.assertTrue(is_structural_nullable("fq_reversal_disp_vs_sweep_disp", policy))
        self.assertTrue(is_structural_nullable("metric_range_quality", policy))
        self.assertTrue(is_structural_nullable("tech_engine_range_width_atr", policy))
        self.assertTrue(is_structural_nullable("tech_premium_discount_position_pct", policy))
        self.assertTrue(is_structural_nullable("tech_long_nearest_level_age_bars", policy))
        self.assertTrue(is_structural_nullable("tech_nearest_active_sh_above_distance_atr20", policy))
        self.assertTrue(is_structural_nullable("tech_nearest_active_isl_below_distance_atr20", policy))
        self.assertFalse(is_structural_nullable("macro_signal_count_24h", policy))

    def test_signal_inference_normalizes_v1_decision_fields(self) -> None:
        from v2_run_signal_inference import normalize_decision_row

        row = normalize_decision_row(
            {
                "tds_decision_class": "high_conviction",
                "tds_entry_permission": "conditional_take_candidate",
                "tds_trade_action": "paper_trade_candidate_manual_confirm",
                "tds_reason": "score>=0.5",
                "score": "0.61",
            }
        )

        self.assertEqual(row["bucket"], "high_conviction")
        self.assertEqual(row["permission"], "review")
        self.assertEqual(row["trade_system_action"], "paper_trade_candidate_manual_confirm")
        self.assertEqual(row["model_score"], "0.61")
        self.assertEqual(row["reason_codes"], "score>=0.5")
        self.assertEqual(row["decision_source"], "approved_v1_artifacts_v2_normalized")


if __name__ == "__main__":
    unittest.main()
