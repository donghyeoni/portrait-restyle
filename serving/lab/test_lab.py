"""python -m unittest serving.lab.test_lab — GPU·ComfyUI 없이 도는 부분만."""
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from engines import kontext
from serving.lab import runner, worker


class CleanParamsTest(unittest.TestCase):
    def test_keeps_known_keys_in_range(self):
        out = runner.clean_params("pulid", {"seed": "7", "guidance": 3.5, "glasses": "on", "prompt": " neon ", "x": 1})
        self.assertEqual(out, {"seed": 7, "guidance": 3.5, "glasses": "on", "prompt": "neon"})

    def test_rejects_out_of_range(self):
        with self.assertRaises(runner.LabError) as ctx:
            runner.clean_params("kontext", {"steps": 200})
        self.assertEqual(ctx.exception.code, "PARAM_INVALID")

    def test_bool_must_be_bool(self):
        with self.assertRaises(runner.LabError):
            runner.clean_params("kontext", {"lora": "yes"})
        self.assertEqual(runner.clean_params("kontext", {"lora": False}), {"lora": False})

    def test_engine_without_params_keeps_nothing(self):
        self.assertEqual(runner.clean_params("original", {"seed": 1}), {})


class CapturePointsTest(unittest.TestCase):
    def test_spread_and_cap(self):
        self.assertEqual(runner.capture_points(22, 2), [7, 15])
        self.assertEqual(len(runner.capture_points(40, 99)), runner.MAX_CAPTURE)
        self.assertEqual(runner.capture_points(20, 0), [])


class KontextStyleRefTest(unittest.TestCase):
    def test_chain_adds_second_reference_and_keeps_input(self):
        base = kontext.graph("a.png", "p", "t")
        g = runner.with_style_ref(base, "b.png")
        self.assertEqual(g["styleImg"]["inputs"]["image"], "b.png")
        self.assertEqual(g["styleRef"]["inputs"]["conditioning"], ["ref", 0])
        self.assertEqual(g["guid"]["inputs"]["conditioning"], ["styleRef", 0])
        self.assertEqual(g["scale"]["inputs"]["image"], ["img", 0])
        self.assertEqual(base["guid"]["inputs"]["conditioning"], ["ref", 0])   # 운영 그래프는 그대로

    def test_stitch_puts_reference_beside_input(self):
        g = runner.with_style_ref(kontext.graph("a.png", "p", "t"), "b.png", "stitch")
        self.assertEqual(g["stitch"]["inputs"]["image1"], ["img", 0])
        self.assertEqual(g["scale"]["inputs"]["image"], ["stitch", 0])


class DraftStyleTest(unittest.TestCase):
    COMMON = {"QUALITY": "q", "MODEST": "modest", "NECK": "neck", "GLASSES": "g"}

    def test_kontext_prompt_prefers_gendered_text(self):
        style = {"prompt": "base", "prompt_female": "for her"}
        self.assertEqual(runner.style_prompt(style, "female"), "for her")
        self.assertEqual(runner.style_prompt(style, "male"), "base")
        self.assertIsNone(runner.style_prompt({"prompt": "  "}, "male"))
        self.assertIsNone(runner.style_prompt(None, "male"))

    def test_pulid_positive_follows_build_graph_order(self):
        style = {"subject_male": "a knight, {MODEST}", "scene": "castle"}
        self.assertEqual(runner.pulid_positive(style, "female", self.COMMON, glasses_text="g", extra="red cape"),
                         "a knight, modest, red cape, g, castle, q")

    def test_pulid_positive_rejects_missing_scene_and_unknown_slot(self):
        with self.assertRaises(runner.LabError):
            runner.pulid_positive({"subject_male": "a"}, "male", self.COMMON)
        with self.assertRaises(runner.LabError):
            runner.pulid_positive({"subject_male": "a {HAT}", "scene": "s"}, "male", self.COMMON)


class FakeChannel:
    def __init__(self):
        self.acks, self.nacks = [], []

    def basic_ack(self, tag):
        self.acks.append(tag)

    def basic_nack(self, tag, requeue):
        self.nacks.append((tag, requeue))


class Method:
    delivery_tag = 9


def envelope(**payload):
    return json.dumps({"schemaVersion": 1, "eventType": worker.EVENT_TYPE, "traceId": "t1",
                       "payload": {"runId": 42, "engine": "kontext", **payload}}).encode()


class HandleTest(unittest.TestCase):
    def test_wrong_event_is_quarantined(self):
        ch = FakeChannel()
        worker.handle(ch, Method, json.dumps({"schemaVersion": 1, "eventType": "AI_GENERATION_ITEM_REQUESTED",
                                              "payload": {}}).encode())
        self.assertEqual(ch.nacks, [(9, False)])

    def test_success_posts_complete_then_acks(self):
        ch = FakeChannel()
        with mock.patch.object(worker, "process", return_value={"outputs": [], "steps": []}), \
                mock.patch.object(worker.backend, "_post") as post:
            worker.handle(ch, Method, envelope())
        path, body = post.call_args.args
        self.assertEqual(path, "/internal/v1/ai-generation-items/style-lab-runs/42/complete")
        self.assertIn("callbackEventId", body)
        self.assertEqual(ch.acks, [9])

    def test_lab_error_posts_fail(self):
        ch = FakeChannel()
        with mock.patch.object(worker, "process", side_effect=runner.LabError("NO_FACE", "얼굴 없음")), \
                mock.patch.object(worker.backend, "_post") as post:
            worker.handle(ch, Method, envelope())
        path, body = post.call_args.args
        self.assertEqual(path, "/internal/v1/ai-generation-items/style-lab-runs/42/fail")
        self.assertEqual(body["errorCode"], "NO_FACE")
        self.assertEqual(ch.acks, [9])

    def test_callback_4xx_drops_message(self):
        ch = FakeChannel()
        with mock.patch.object(worker, "process", return_value={"outputs": []}), \
                mock.patch.object(worker.backend, "_post", side_effect=worker.backend.BackendError(409, "done")):
            worker.handle(ch, Method, envelope())
        self.assertEqual(ch.acks, [9])


class OutputsTest(unittest.TestCase):
    def test_slots_follow_steps_order(self):
        d = pathlib.Path(tempfile.mkdtemp())
        result = {"reference": "reference.png", "steps": [{"step": 7, "total": 22, "file": "step_7.png"},
                                                           {"step": 15, "total": 22, "file": "step_15.png"}]}
        slots = [s for s, _ in worker._outputs(d, result)]
        self.assertEqual(slots, ["analysis", "reference", "step-1", "step-2", "result", "cutout"])
        self.assertEqual(result["steps"][1], {"step": 15, "total": 22, "slot": "step-2"})


if __name__ == "__main__":
    unittest.main()
