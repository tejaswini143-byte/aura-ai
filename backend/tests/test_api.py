import os
import tempfile
import unittest
from starlette.testclient import TestClient

# Use a fresh temporary DB so tests never overwrite a project or prior test database.
TEST_DB_PATH = tempfile.mktemp(prefix="aura-api-tests-", suffix=".db")
os.environ["DATABASE_PATH"] = TEST_DB_PATH

from app.main import app, init_db
from app import main as aura_main
from app.companion import detect_mood, immediate_safety_response


class TestAuraBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from unittest.mock import patch
        cls.auth_environment = patch.dict(os.environ, {"AURA_ADMIN_PASSWORD": "test-admin-password-with-enough-length", "AURA_ACTIONS_API_TOKEN": ""}, clear=False)
        cls.auth_environment.start()
        init_db()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        try:
            if os.path.exists(TEST_DB_PATH):
                os.remove(TEST_DB_PATH)
        except Exception:
            pass
        cls.auth_environment.stop()

    def test_01_health_and_root(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        resp_root = self.client.get("/")
        self.assertEqual(resp_root.status_code, 200)
        self.assertEqual(resp_root.json()["version"], "0.3.0")
        self.assertIn("SmartOpportunityRadar", resp_root.json()["modules"])

    def test_companion_mood_safety_preferences_and_provider_status(self):
        login = self.client.post("/api/auth/login", json={"password": "test-admin-password-with-enough-length"}, headers={"Origin": "http://localhost:3000"})
        self.assertEqual(login.status_code, 200)
        csrf = login.json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"}
        self.assertEqual(detect_mood("I feel lonely today"), "lonely")
        self.assertEqual(detect_mood("ఈ రోజు చాలా సంతోషంగా ఉంది"), "happy")
        self.assertIsNotNone(immediate_safety_response("I might hurt myself"))
        before = self.client.get("/api/companion/preferences")
        self.assertEqual(before.status_code, 200)
        update = {**before.json(), "preferred_language": "Telugu", "personality": "Sis",
                  "mixed_language": True, "destination_phone_present": False,
                  "destination_phone_masked": None, "phone_verified": False,
                  "store_phone_with_consent": False, "clear_destination_phone": False}
        update.pop("id", None); update.pop("updated_at", None)
        saved = self.client.put("/api/companion/preferences", json=update, headers=headers)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["preferred_language"], "Telugu")
        self.assertFalse(saved.json()["phone_verified"])
        self.assertEqual(self.client.put("/api/companion/preferences", json={**update, "automatic_mood_calls": True}, headers=headers).status_code, 422)
        status = self.client.get("/api/providers/status")
        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.json()["twilio"]["manual_calls_enabled"])

    def test_companion_chat_safety_path_does_not_call_gemini(self):
        class ShouldNotCallGemini:
            def __init__(self):
                raise AssertionError("Safety response must not call the model")
        old = aura_main.GeminiService
        aura_main.GeminiService = ShouldNotCallGemini
        try:
            response = self.client.post("/api/companion/chat", json={"message": "I want to end my life"})
        finally:
            aura_main.GeminiService = old
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"], "safety_fallback")
        self.assertFalse(response.json()["automatic_call_triggered"])

    def test_companion_chat_uses_fallback_when_provider_unavailable(self):
        from unittest.mock import patch, AsyncMock
        from app.ai_service import GeminiProviderError
        mock = AsyncMock(side_effect=GeminiProviderError("provider unavailable"))
        with patch("app.main.GeminiService.generate", mock):
            response = self.client.post("/api/companion/chat", json={"message": "I feel stressed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider"], "fallback")
        self.assertFalse(response.json()["call_offer"])
        self.assertIn("verified-number workflow", response.json()["call_unavailable_reason"])
        self.assertFalse(response.json()["automatic_call_triggered"])

    def test_02_global_stats(self):
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("tasks", data)
        self.assertIn("opportunities", data)
        self.assertIn("completion_rate_pct", data["tasks"])

    def test_03_tasks_crud_and_validation(self):
        # 1. Create task
        res_create = self.client.post("/api/tasks", json={
            "title": "Study Graph Algorithms for Hackathon",
            "description": "Shortest path and min-cut max-flow problems.",
            "category": "Academic",
            "priority": "High",
            "status": "In Progress"
        })
        self.assertEqual(res_create.status_code, 201)
        task = res_create.json()
        task_id = task["id"]
        self.assertEqual(task["title"], "Study Graph Algorithms for Hackathon")
        self.assertEqual(task["priority"], "High")

        # 2. Get task by ID
        res_get = self.client.get(f"/api/tasks/{task_id}")
        self.assertEqual(res_get.status_code, 200)

        # 3. Update task
        res_up = self.client.patch(f"/api/tasks/{task_id}", json={
            "priority": "Urgent",
            "status": "Completed"
        })
        self.assertEqual(res_up.status_code, 200)
        self.assertEqual(res_up.json()["priority"], "Urgent")
        self.assertEqual(res_up.json()["status"], "Completed")

        # 4. Filter tasks
        res_filter = self.client.get("/api/tasks?status=Completed")
        self.assertEqual(res_filter.status_code, 200)
        self.assertTrue(any(t["id"] == task_id for t in res_filter.json()))

        # 5. Delete task
        res_del = self.client.delete(f"/api/tasks/{task_id}")
        self.assertEqual(res_del.status_code, 200)

    def test_04_smart_opportunity_radar_lifecycle(self):
        # 1. Create an opportunity with full metadata
        opp_payload = {
            "title": "CERN Summer Student Fellowship 2027",
            "organization": "CERN European Organization for Nuclear Research",
            "type": "Fellowship",
            "deadline": "2027-01-31",
            "url": "https://careers.cern/summer-student-programme",
            "location": "Geneva, Switzerland",
            "remote_mode": "On-site",
            "description": "Prestigious 8-13 week student research fellowship working on experimental physics and high performance software.",
            "eligibility": "Undergraduate or master students with at least 3 years of university studies.",
            "skills": "C++, Python, Linux, Data Analysis",
            "source": "Official CERN Portal",
            "stipend": "CHF 90/day living allowance + travel costs",
            "status": "Saved",
            "is_verified": 1
        }
        res_create = self.client.post("/api/opportunities", json=opp_payload)
        self.assertEqual(res_create.status_code, 201)
        created = res_create.json()
        opp_id = created["id"]
        self.assertEqual(created["type"], "Fellowship")
        self.assertEqual(created["location"], "Geneva, Switzerland")
        self.assertEqual(created["is_verified"], 1)

        # 2. Duplicate detection check (same title + organization) -> 409
        res_dup = self.client.post("/api/opportunities", json=opp_payload)
        self.assertEqual(res_dup.status_code, 409)

        # 3. Retrieve opportunity with linked tasks
        res_get = self.client.get(f"/api/opportunities/{opp_id}")
        self.assertEqual(res_get.status_code, 200)
        self.assertIn("linked_tasks", res_get.json())

        # 4. Search and filter opportunities
        res_search = self.client.get("/api/opportunities?search=CERN&type=Fellowship")
        self.assertEqual(res_search.status_code, 200)
        self.assertTrue(any(o["id"] == opp_id for o in res_search.json()))

        # 5. Update opportunity status
        res_status = self.client.patch(f"/api/opportunities/{opp_id}/status", json={"status": "Interested"})
        self.assertEqual(res_status.status_code, 200)
        self.assertEqual(res_status.json()["status"], "Interested")

        # 6. Delete opportunity
        res_del = self.client.delete(f"/api/opportunities/{opp_id}")
        self.assertEqual(res_del.status_code, 200)

    def test_05_opportunity_to_task_planning(self):
        # Create an opportunity
        opp_res = self.client.post("/api/opportunities", json={
            "title": "OpenAI AI Agents Hackathon",
            "organization": "OpenAI & Partners",
            "type": "Hackathon",
            "deadline": "2026-11-20",
            "url": "https://hackathon.openai.com",
            "skills": "Python, Prompt Engineering, Agents"
        })
        self.assertEqual(opp_res.status_code, 201)
        opp_id = opp_res.json()["id"]

        # 1. Get suggested checklist tasks
        res_suggest = self.client.get(f"/api/opportunities/{opp_id}/suggested-tasks")
        self.assertEqual(res_suggest.status_code, 200)
        suggested = res_suggest.json()
        self.assertIn("suggested_tasks", suggested)
        self.assertTrue(len(suggested["suggested_tasks"]) >= 3)

        # 2. Approve and create preparation tasks from checklist
        tasks_to_create = [
            {
                "title": f"Review API docs for {suggested['opportunity_title']}",
                "description": "Understand authentication and rate limit requirements.",
                "priority": "High"
            },
            {
                "title": f"Build prototype agent logic for {suggested['opportunity_title']}",
                "description": "Implement core reasoning engine.",
                "priority": "Urgent"
            }
        ]
        res_create_tasks = self.client.post(
            f"/api/opportunities/{opp_id}/create-tasks",
            json={"tasks": tasks_to_create}
        )
        self.assertEqual(res_create_tasks.status_code, 201)
        self.assertEqual(res_create_tasks.json()["tasks_created_count"], 2)

        # 3. Verify tasks are in LifePilot and linked to opportunity
        res_tasks = self.client.get(f"/api/tasks?opportunity_id={opp_id}")
        self.assertEqual(res_tasks.status_code, 200)
        self.assertEqual(len(res_tasks.json()), 2)

        # Cleanup
        self.client.delete(f"/api/opportunities/{opp_id}")

    def test_06_career_roadmap(self):
        # 1. Get default career roadmap
        res_get = self.client.get("/api/career/roadmap")
        self.assertEqual(res_get.status_code, 200)
        data = res_get.json()
        self.assertIn("target_role", data)
        self.assertIn("roadmap", data)

        # 2. Save customized career goals
        save_payload = {
            "target_role": "AI Research Scientist & Agent Architect",
            "target_industry": "Autonomous AI Systems",
            "current_skills": "Python, PyTorch, Next.js, FastAPI",
            "target_skills": "Multimodal Agents, RLHF, Voice Telephony",
            "roadmap_json": '[{"id":"step1","title":"Complete Capstone","status":"In Progress"}]'
        }
        res_save = self.client.post("/api/career/roadmap", json=save_payload)
        self.assertEqual(res_save.status_code, 200)
        self.assertEqual(res_save.json()["target_role"], save_payload["target_role"])

    def test_07_briefing_prioritization(self):
        # Test transparent next-move logic
        res = self.client.get("/api/briefing")
        self.assertEqual(res.status_code, 200)
        self.assertIn("headline", res.json())
        self.assertIn("rule", res.json())

    def test_08_call_consent_guardrail(self):
        resp = self.client.post("/api/calls", json={
            "phone": "+919876543210",
            "purpose": "Milestone accountability follow-up",
            "consent": False
        })
        self.assertEqual(resp.status_code, 400)


    def test_milestone6_gemini_missing_key_is_safe(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            response = self.client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Plan my day"}]})
        self.assertEqual(response.status_code, 503)
        self.assertIn("GEMINI_API_KEY", response.json()["detail"])

    def test_milestone6_chat_uses_mock_and_saved_data(self):
        from unittest.mock import patch, AsyncMock
        task = self.client.post("/api/tasks", json={"title": "Read project brief", "category": "Academic", "priority": "High"})
        self.assertEqual(task.status_code, 201)
        mock = AsyncMock(return_value="Suggestion: read the project brief first.")
        with patch("app.main.GeminiService.generate", mock):
            response = self.client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "What should I do?"}]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["read_only"])
        self.assertEqual(response.json()["reply"], "Suggestion: read the project brief first.")
        args = mock.await_args.args
        self.assertIn("Read project brief", str(args[1]))

    def test_milestone6_automation_settings_plan_and_discovery(self):
        settings = self.client.get("/api/automation/settings")
        self.assertEqual(settings.status_code, 200)
        plan1 = self.client.post("/api/automation/plan/run")
        plan2 = self.client.post("/api/automation/plan/run")
        self.assertEqual(plan1.status_code, 200)
        self.assertTrue(plan1.json()["created"])
        self.assertFalse(plan2.json()["created"])
        discovery = self.client.get("/api/opportunities/discovery")
        self.assertEqual(discovery.status_code, 200)
        self.assertFalse(discovery.json()["available"])

    def test_milestone6_reminders_deduplicate_and_disable(self):
        task = self.client.post("/api/tasks", json={"title": "Reminder test", "category": "Personal", "priority": "Medium"}).json()
        payload = {"scheduled_for": "2030-01-01T09:00:00"}
        first = self.client.post(f"/api/tasks/{task['id']}/reminders", json=payload)
        second = self.client.post(f"/api/tasks/{task['id']}/reminders", json=payload)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["id"], second.json()["id"])
        off = self.client.put("/api/automation/settings", json={"reminders_enabled": False, "reminder_lead_minutes": 30, "daily_planning_enabled": False, "daily_planning_time": "09:00", "timezone": "Asia/Kolkata"})
        self.assertEqual(off.status_code, 200)
        self.assertEqual(self.client.get("/api/reminders?include_history=false").json(), [])


    def test_milestone6_provider_error_is_safe(self):
        from unittest.mock import patch, AsyncMock
        from app.ai_service import GeminiProviderError
        mock = AsyncMock(side_effect=GeminiProviderError("provider unavailable"))
        with patch("app.main.GeminiService.generate", mock):
            response = self.client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Plan my day"}]})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], "provider unavailable")

    def test_milestone6_due_reminder_becomes_in_app_ready(self):
        from app.main import automation
        task = self.client.post("/api/tasks", json={"title": "Due reminder test", "category": "Personal", "priority": "High", "due_at": "2020-01-01T09:00:00+00:00"})
        self.assertEqual(task.status_code, 201)
        self.assertGreaterEqual(automation.process_due_reminders(), 1)
        reminders = self.client.get("/api/reminders?include_history=false").json()
        self.assertTrue(any(item["task_id"] == task.json()["id"] and item["status"] == "ready" for item in reminders))


if __name__ == "__main__":
    unittest.main()
