import os
import sys
import sqlite3
import datetime
import unittest
from fastapi.testclient import TestClient

# Ensure the app imports work properly
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

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
        # Wait, get_dashboard automatically inserts a session in 'PendingInquiry' status if none exists.
        # So it should be checked in automatically. Let's verify that.
        self.assertIn("Status: Checked in at Checkout Counter", response.text)
        
        # Case 2: Session is Done
        execute_db("UPDATE checkout_sessions SET Status = 'Done' WHERE MemberID = ?", (self.test_member_id,))
        response = self.client.get(f"/dashboard?member_id={self.test_member_id}")
        self.assertEqual(response.status_code, 200)
        # Should render 'Not checked in' and the 'Check In' button
        self.assertIn("Status: Not checked in at Checkout Counter", response.text)
        self.assertIn("arriveAtCheckoutStation()", response.text)
        self.assertIn("Check In", response.text)

if __name__ == "__main__":
    unittest.main()
