import os
import sys
import sqlite3
import datetime
import unittest
from fastapi.testclient import TestClient
from unittest.mock import patch
import google.adk.agents

# Ensure the app imports work properly
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
backend_path = os.path.join(current_dir, "signalsense_enterprise")
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from member_ambassador_app.main import app, get_db_path, query_db, execute_db

class TestCheckoutStateMachine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # We will use the existing database but create a test member for isolation
        cls.test_member_id = "M9999"
        cls.client = TestClient(app)
        
        # Generate and set session token cookie for mock HTTP calls
        from member_ambassador_app.jwt_helper import create_access_token
        token = create_access_token({"member_id": cls.test_member_id, "role": "Member"})
        cls.client.cookies.set("session_token", token)
        
        # Insert a clean test member if not exists
        execute_db(
            "INSERT OR IGNORE INTO members (MemberID, Name, SamsPoints, TrustScore, Ambassador) "
            "VALUES (?, 'Test User', 10, 80, 'No')",
            (cls.test_member_id,)
        )

    def setUp(self):
        # Clean up any leftover sessions for the test member before each test
        execute_db("DELETE FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,))

    def tearDown(self):
        # Clean up sessions after each test
        execute_db("DELETE FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,))

    @classmethod
    def tearDownClass(cls):
        # Final cleanup of test member and their sessions
        execute_db("DELETE FROM checkout_sessions WHERE MemberID = ?", (cls.test_member_id,))
        execute_db("DELETE FROM members WHERE MemberID = ?", (cls.test_member_id,))

    def test_checkout_arrival(self):
        """Test that arriving at the checkout counter creates a ReadyToCheckout session."""
        response = self.client.post("/checkout/arrive", data={"member_id": self.test_member_id})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        
        # Verify database state
        session = query_db("SELECT Status FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,), one=True)
        self.assertIsNotNone(session)
        self.assertEqual(session["Status"], "ReadyToCheckout")

    def test_poll_member_transitions_pending_to_inquiry_sent(self):
        """Test that polling a PendingInquiry session transitions it to InquirySent."""
        # Pre-populate session with PendingInquiry
        execute_db(
            "INSERT INTO checkout_sessions (MemberID, Status, AssociateQuestion, LastUpdated) "
            "VALUES (?, 'PendingInquiry', 'Test Question', ?)",
            (self.test_member_id, datetime.datetime.now().isoformat())
        )
        
        # Poll the session
        response = self.client.get(f"/checkout/poll-member?member_id={self.test_member_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "PendingInquiry")
        self.assertEqual(data["associate_question"], "Test Question")
        
        # Verify status transitioned to InquirySent in the DB
        session = query_db("SELECT Status FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,), one=True)
        self.assertEqual(session["Status"], "InquirySent")

    def test_respond_member_response(self):
        """Test that submitting member checkout responses updates status to ResponseReceived."""
        # Pre-populate session with InquirySent
        execute_db(
            "INSERT INTO checkout_sessions (MemberID, Status, AssociateQuestion, LastUpdated) "
            "VALUES (?, 'InquirySent', 'Test Question', ?)",
            (self.test_member_id, datetime.datetime.now().isoformat())
        )
        
        # Respond
        response = self.client.post(
            "/checkout/respond-member",
            data={
                "member_id": self.test_member_id,
                "response": "No, I did not find apples",
                "field": "response"
            }
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify status in DB
        session = query_db("SELECT Status, MemberResponse FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,), one=True)
        self.assertEqual(session["Status"], "ResponseReceived")
        self.assertEqual(session["MemberResponse"], "No, I did not find apples")

    def test_respond_member_enrollment(self):
        """Test that submitting ambassador enrollment answers updates status to EnrollmentResponseReceived."""
        # Pre-populate session with EnrollmentProposed
        execute_db(
            "INSERT INTO checkout_sessions (MemberID, Status, AssociateQuestion, LastUpdated) "
            "VALUES (?, 'EnrollmentProposed', 'Would you like to join?', ?)",
            (self.test_member_id, datetime.datetime.now().isoformat())
        )
        
        # Respond Yes
        response = self.client.post(
            "/checkout/respond-member",
            data={
                "member_id": self.test_member_id,
                "response": "Yes",
                "field": "enrollment"
            }
        )
        self.assertEqual(response.status_code, 200)
        
        # Verify status in DB
        session = query_db("SELECT Status, EnrollmentAnswer FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,), one=True)
        self.assertEqual(session["Status"], "EnrollmentResponseReceived")
        self.assertEqual(session["EnrollmentAnswer"], "Yes")

    def test_checkout_complete_close(self):
        """Test that complete-close transitions status to Done."""
        # Pre-populate session with Completed
        execute_db(
            "INSERT INTO checkout_sessions (MemberID, Status, AssociateQuestion, LastUpdated) "
            "VALUES (?, 'Completed', 'Your checkout is complete!', ?)",
            (self.test_member_id, datetime.datetime.now().isoformat())
        )
        
        # Complete/close session
        response = self.client.post("/checkout/complete-close", data={"member_id": self.test_member_id})
        self.assertEqual(response.status_code, 200)
        
        # Verify status in DB
        session = query_db("SELECT Status FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,), one=True)
        self.assertEqual(session["Status"], "Done")

    def test_dashboard_renders_not_checked_in_correctly(self):
        """Test that the dashboard correctly renders the 'Not checked in' status and Check In button when status is Done or None."""
        # Case 1: No session exists
        response = self.client.get(f"/dashboard?member_id={self.test_member_id}")
        self.assertEqual(response.status_code, 200)
        # Should render 'Not checked in' and the 'Check Out Counter' button because we no longer check in automatically
        self.assertIn("Status: Not checked in at Checkout Counter", response.text)
        self.assertIn("arriveAtCheckoutStation()", response.text)
        self.assertIn("Check Out Counter", response.text)
        
        # Case 2: Session is Done
        execute_db("UPDATE checkout_sessions SET Status = 'Done' WHERE MemberID = ?", (self.test_member_id,))
        response = self.client.get(f"/dashboard?member_id={self.test_member_id}")
        self.assertEqual(response.status_code, 200)
        # Should render 'Not checked in' and the 'Check Out Counter' button
        self.assertIn("Status: Not checked in at Checkout Counter", response.text)
        self.assertIn("arriveAtCheckoutStation()", response.text)
        self.assertIn("Check Out Counter", response.text)

from operations_dashboard.main import app as ops_app

class TestNewFeatureStateFlows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_member_id = "M9999"
        cls.member_client = TestClient(app)
        cls.ops_client = TestClient(ops_app)
        
        # Authorize member client
        from member_ambassador_app.jwt_helper import create_access_token as member_token
        m_tok = member_token({"member_id": cls.test_member_id, "role": "Member"})
        cls.member_client.cookies.set("session_token", m_tok)
        
        # Authorize associate client for ops dashboard
        from operations_dashboard.jwt_helper import create_access_token as ops_token
        o_tok = ops_token({"role": "Checkout_Associate"})
        cls.ops_client.cookies.set("session_token", o_tok)
        
        # Ensure test member exists
        execute_db(
            "INSERT OR IGNORE INTO members (MemberID, Name, SamsPoints, TrustScore, Ambassador) "
            "VALUES (?, 'Test User', 10, 80, 'No')",
            (cls.test_member_id,)
        )

    def setUp(self):
        execute_db("DELETE FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,))

    def tearDown(self):
        execute_db("DELETE FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,))
        execute_db("DELETE FROM candidate_products WHERE MemberIDProposer = ?", (self.test_member_id,))
        execute_db("DELETE FROM signals WHERE MemberID = ?", (self.test_member_id,))

    @classmethod
    def tearDownClass(cls):
        execute_db("DELETE FROM checkout_sessions WHERE MemberID = ?", (cls.test_member_id,))
        execute_db("DELETE FROM members WHERE MemberID = ?", (cls.test_member_id,))

    def test_debug_reset_ambassadors(self):
        """Test that the reset-ambassadors endpoint sets Ambassador status to No and redirects to success."""
        execute_db("UPDATE members SET Ambassador = 'Yes' WHERE MemberID = ?", (self.test_member_id,))
        
        response = self.member_client.post("/debug/reset-ambassadors", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("success=", response.headers.get("location"))
        
        member = query_db("SELECT Ambassador FROM members WHERE MemberID = ?", (self.test_member_id,), one=True)
        self.assertEqual(member["Ambassador"], "No")

    def test_propose_proposal_endpoint(self):
        """Test that propose-proposal endpoint sets status to ProposalProposed."""
        execute_db(
            "INSERT INTO checkout_sessions (MemberID, Status, LastUpdated) VALUES (?, 'InquirySent', ?)",
            (self.test_member_id, datetime.datetime.now().isoformat())
        )
        
        response = self.ops_client.post(
            "/checkout/propose-proposal",
            data={"member_id": self.test_member_id, "item_name": "Kimchi"}
        )
        self.assertEqual(response.status_code, 200)
        
        session = query_db("SELECT Status, AssociateQuestion, MemberResponse FROM checkout_sessions WHERE MemberID = ?", (self.test_member_id,), one=True)
        self.assertEqual(session["Status"], "ProposalProposed")
        self.assertIn("Kimchi", session["AssociateQuestion"])
        self.assertEqual(session["MemberResponse"], "Kimchi")

    def test_submit_proposal_from_checkout(self):
        """Test that submit-proposal-from-checkout creates candidate product and signal."""
        execute_db(
            "INSERT INTO checkout_sessions (MemberID, Status, MemberResponse, LastUpdated) VALUES (?, 'ProposalProposed', 'Kimchi', ?)",
            (self.test_member_id, datetime.datetime.now().isoformat())
        )
        
        response = self.ops_client.post(
            "/checkout/submit-proposal-from-checkout",
            data={"member_id": self.test_member_id}
        )
        self.assertEqual(response.status_code, 200)
        
        cand = query_db("SELECT CandidateID, ItemDescription FROM candidate_products WHERE MemberIDProposer = ?", (self.test_member_id,), one=True)
        self.assertIsNotNone(cand)
        self.assertIn("Kimchi", cand["ItemDescription"])
        
        sig = query_db("SELECT SignalID, SignalType, CandidateID FROM signals WHERE MemberID = ?", (self.test_member_id,), one=True)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["SignalType"], "ProductSuggestion")
        self.assertEqual(sig["CandidateID"], cand["CandidateID"])

    @patch("google.adk.workflow._llm_agent_wrapper.run_llm_agent_as_node")
    def test_process_member_voice_suggestion(self, mock_run_node):
        """Test that submitting a voice signal to suggest a product successfully runs the agent flow."""
        from google.adk.events.event import Event
        
        async def mock_run_node_impl(agent, ctx, node_input, *args, **kwargs):
            if agent.name == "voice_classifier":
                from signalsense_agent.agent import VoiceAnalysis
                analysis = VoiceAnalysis(
                    intent="ProductSuggestion",
                    extracted_item="durian",
                    is_negative_sentiment=False,
                    explanation="Mocked suggestion classifier"
                )
                ctx.actions.state_delta[agent.output_key] = analysis
                ctx.state[agent.output_key] = analysis
                yield Event(output=analysis)
            else:
                yield Event(output={})
                
        mock_run_node.side_effect = mock_run_node_impl
        
        response = self.member_client.post(
            "/process-member-voice",
            data={
                "member_id": self.test_member_id,
                "transcript": "I want to have durian in the store"
            },
            follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("success=", response.headers.get("location"))
        
        cand = query_db("SELECT CandidateID, ItemDescription FROM candidate_products WHERE MemberIDProposer = ?", (self.test_member_id,), one=True)
        self.assertIsNotNone(cand)
        self.assertIn("durian", cand["ItemDescription"].lower())

    @patch("google.adk.workflow._llm_agent_wrapper.run_llm_agent_as_node")
    def test_process_member_voice_uncarried_oos_suggestion(self, mock_run_node):
        """Test that reporting a missing item we don't carry is treated as a new product suggestion."""
        from google.adk.events.event import Event
        
        async def mock_run_node_impl(agent, ctx, node_input, *args, **kwargs):
            if agent.name == "voice_classifier":
                from signalsense_agent.agent import VoiceAnalysis
                analysis = VoiceAnalysis(
                    intent="OOS",
                    extracted_item="durian",
                    is_negative_sentiment=True,
                    explanation="Mocked OOS for uncarried item"
                )
                ctx.actions.state_delta[agent.output_key] = analysis
                ctx.state[agent.output_key] = analysis
                yield Event(output=analysis)
            else:
                yield Event(output={})
                
        mock_run_node.side_effect = mock_run_node_impl
        
        response = self.member_client.post(
            "/process-member-voice",
            data={
                "member_id": self.test_member_id,
                "transcript": "I couldn't find durian today"
            },
            follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("success=", response.headers.get("location"))
        
        cand = query_db("SELECT CandidateID, ItemDescription FROM candidate_products WHERE MemberIDProposer = ?", (self.test_member_id,), one=True)
        self.assertIsNotNone(cand)
        self.assertIn("durian", cand["ItemDescription"].lower())

if __name__ == "__main__":
    unittest.main()
