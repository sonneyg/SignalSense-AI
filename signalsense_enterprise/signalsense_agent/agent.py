import os
import json
import sqlite3
from signalsense_agent.db_helper import get_db_conn
import datetime
import random
from typing import Any, Union, Dict, Optional, List
from pydantic import BaseModel, Field

from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.apps import App
from google.adk.events.event import Event
from google.adk.workflow import Workflow, node, RetryConfig

# ------------------------------------------------------------------------------
# 1. Config & Database Paths
# ------------------------------------------------------------------------------

class Config:
    MODEL: str = os.getenv("SIGNAL_MODEL", "gemini-2.5-flash")

CONFIG = Config()

def get_db_path() -> str:
    db_path = os.getenv("DB_PATH")
    if db_path:
        db_dir = os.path.dirname(db_path)
        if db_dir and os.path.exists(db_dir):
            return db_path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.dirname(os.path.dirname(current_dir))
    db_path = os.path.join(workspace_root, "enterprise_db", "enterprise.db")
    if os.path.exists(db_path):
        return db_path
    return "enterprise.db"

# ------------------------------------------------------------------------------
# 2. Schemas & Models
# ------------------------------------------------------------------------------

class SignalInput(BaseModel):
    """Raw input event containing the signal payload (supports nested and raw structures)."""
    data: Any = None
    signal_type: Optional[str] = None
    member_id: Optional[str] = None
    club_id: Optional[str] = None
    item_id: Optional[str] = None
    candidate_id: Optional[str] = None
    description: Optional[str] = None
    store_where_found: Optional[str] = None
    reason: Optional[str] = None
    action: Optional[str] = None
    signal_id: Optional[str] = None
    next_threshold: Optional[int] = None
    launch_date: Optional[str] = None
    free_unit_eligible_count: Optional[int] = None
    expiration_window: Optional[str] = None
    archive_reason: Optional[str] = None
    resolution_plan: Optional[str] = None
    forecast_adjustment: Optional[str] = None
    transfer_source: Optional[str] = None
    supply_constraint: Optional[str] = None

class SuggestionAnalysis(BaseModel):
    """Structured output for the LLM product suggestion classifier."""
    is_appropriate: bool = Field(description="True if the product description is safe and does not violate policies (no profanity, no nonsense, no offensive content).")
    department: str = Field(description="The retail department this item belongs to (e.g., Produce, Grocery, Bakery, Meat, Seafood, Dairy, Frozen, Apparel, Household, Pharmacy, Electronics).")
    suggested_category: str = Field(description="A suggested category name for the product (e.g. Snacks, Fresh Fruit, Beverages).")
    reasoning: str = Field(description="Brief explanation of the appropriateness check and department categorization.")

class SignalState(BaseModel):
    """Persisted state for the session workflow."""
    signal_type: Optional[str] = None
    member_id: Optional[str] = None
    club_id: Optional[str] = None
    item_id: Optional[str] = None
    candidate_id: Optional[str] = None
    description: Optional[str] = None
    store_where_found: Optional[str] = None
    reason: Optional[str] = None
    action: Optional[str] = None
    signal_id: Optional[str] = None
    next_threshold: Optional[int] = None
    launch_date: Optional[str] = None
    free_unit_eligible_count: Optional[int] = None
    expiration_window: Optional[str] = None
    archive_reason: Optional[str] = None
    resolution_plan: Optional[str] = None
    forecast_adjustment: Optional[str] = None
    transfer_source: Optional[str] = None
    supply_constraint: Optional[str] = None
    
    # Analysis outputs
    is_appropriate: Optional[bool] = None
    department: Optional[str] = None
    suggested_category: Optional[str] = None
    analysis_reasoning: Optional[str] = None
    suggestion_analysis: Optional[Union[SuggestionAnalysis, Dict[str, Any]]] = None
    voice_analysis: Optional[Any] = None
    
    # Processing outcomes
    status: Optional[str] = None
    outcome_message: Optional[str] = None
    points_awarded: int = 0
    trust_increase: int = 0

class SignalOutput(BaseModel):
    """Final output schema returned by the ADK Agent."""
    signal_type: str
    status: str
    message: str
    points_awarded: int
    trust_increase: int

# ------------------------------------------------------------------------------
# 3. LLM Analyzer Agent
# ------------------------------------------------------------------------------

def get_analyzer_instruction(ctx: Context) -> str:
    return (
        "You are an expert retail merchant classifier. Analyze the member's new product suggestion description. "
        "1. Check if the description is appropriate (no profanity, no spam, no nonsense). "
        "2. Classify the product into a standard retail department (e.g., Produce, Grocery, Bakery, Meat, Seafood, Dairy, Frozen, Apparel, Household, Pharmacy, Electronics). "
        "3. Suggest a specific category name (e.g., Organic Vegetables, Korean Snacks, Beverages)."
    )

suggestion_analyzer = LlmAgent(
    name="suggestion_analyzer",
    model=CONFIG.MODEL,
    instruction=get_analyzer_instruction,
    output_schema=SuggestionAnalysis,
    output_key="suggestion_analysis",
    retry_config=RetryConfig(max_attempts=3)
)

class VoiceAnalysis(BaseModel):
    """Structured output for the LLM voice response classifier."""
    intent: str = Field(description="Must be either 'OOS' or 'ProductSuggestion'.")
    extracted_item: str = Field(description="The name of the item or product mentioned by the member (e.g. 'organic banana', 'kimchi', 'gala apple').")
    is_negative_sentiment: bool = Field(description="True if the user indicates they could not find the item, it was missing, or they are unhappy about stock levels.")
    explanation: str = Field(description="Brief explanation of the classification decision.")

def get_voice_classifier_instruction(ctx: Context) -> str:
    return (
        "You are an AI checkout assistant that classifies the member's voice response. "
        "Analyze the transcript and determine: "
        "1. Is the member reporting an out-of-stock item (intent='OOS') or suggesting a new product they wish we carried (intent='ProductSuggestion')? "
        "2. Extract the specific item name mentioned. "
        "3. Is the sentiment negative (did not find it / missing)?"
    )

voice_classifier = LlmAgent(
    name="voice_classifier",
    model=CONFIG.MODEL,
    instruction=get_voice_classifier_instruction,
    output_schema=VoiceAnalysis,
    output_key="voice_analysis",
    retry_config=RetryConfig(max_attempts=3)
)

# ------------------------------------------------------------------------------
# 4. Workflow Nodes
# ------------------------------------------------------------------------------


def extract_signal(node_input: SignalInput) -> Event:
    """Extracts, normalizes, and stores the incoming signal payload in state."""
    data = node_input.data
    payload = {}
    
    if data is not None:
        if isinstance(data, str):
            try:
                payload = json.loads(data)
            except Exception:
                payload = {"message": data}
        elif isinstance(data, dict):
            payload = data
    else:
        payload = node_input.model_dump()
        
    if "input" in payload and isinstance(payload["input"], dict):
        payload = payload["input"]
        
    signal_type = str(payload.get("signal_type") or payload.get("SignalType") or "")
    member_id = str(payload.get("member_id") or payload.get("MemberID") or "")
    club_id = str(payload.get("club_id") or payload.get("ClubID") or "")
    item_id = str(payload.get("item_id") or payload.get("ItemID") or "")
    candidate_id = str(payload.get("candidate_id") or payload.get("CandidateID") or "")
    description = str(payload.get("description") or payload.get("ItemDescription") or "")
    store = str(payload.get("store_where_found") or payload.get("StoreWhereFound") or payload.get("store") or "")
    reason = str(payload.get("reason") or payload.get("Reason") or "")
    
    action = str(payload.get("action") or "")
    signal_id = str(payload.get("signal_id") or "")
    next_threshold = payload.get("next_threshold")
    launch_date = payload.get("launch_date")
    free_count = payload.get("free_unit_eligible_count")
    expiration = payload.get("expiration_window")
    arch_reason = payload.get("archive_reason")
    res_plan = payload.get("resolution_plan")
    fore_adj = payload.get("forecast_adjustment")
    tf_source = payload.get("transfer_source")
    sup_const = payload.get("supply_constraint")
    
    def clean(val):
        return None if val == "" or val == "None" or val == "NULL" else val

    cleaned_payload = {
        "signal_type": clean(signal_type),
        "member_id": clean(member_id),
        "club_id": clean(club_id),
        "item_id": clean(item_id),
        "candidate_id": clean(candidate_id),
        "description": clean(description),
        "store_where_found": clean(store),
        "reason": clean(reason),
        "action": clean(action),
        "signal_id": clean(signal_id),
        "next_threshold": int(next_threshold) if next_threshold is not None and str(next_threshold).isdigit() else None,
        "launch_date": clean(str(launch_date)) if launch_date is not None else None,
        "free_unit_eligible_count": int(free_count) if free_count is not None and str(free_count).isdigit() else None,
        "expiration_window": clean(str(expiration)) if expiration is not None else None,
        "archive_reason": clean(str(arch_reason)) if arch_reason is not None else None,
        "resolution_plan": clean(str(res_plan)) if res_plan is not None else None,
        "forecast_adjustment": clean(str(fore_adj)) if fore_adj is not None else None,
        "transfer_source": clean(str(tf_source)) if tf_source is not None else None,
        "supply_constraint": clean(str(sup_const)) if sup_const is not None else None
    }
    
    return Event(
        output=SignalInput(**cleaned_payload),
        route=cleaned_payload["signal_type"],
        state=cleaned_payload
    )

def process_oos(ctx: Context, node_input: SignalInput) -> Event:
    """Processes an Out-of-Stock (OOS) report, updating inventory and awarding points."""
    conn = get_db_conn()
    cursor = conn.cursor()
    
    member_id = ctx.state.get("member_id")
    club_id = ctx.state.get("club_id")
    item_id = ctx.state.get("item_id")
    
    # Check if member is an Ambassador
    cursor.execute("SELECT Ambassador FROM members WHERE MemberID = ?", (member_id,))
    m_row = cursor.fetchone()
    is_ambassador = m_row and m_row[0] == "Yes"
    
    # 1. Verify if the item is in the club's inventory and has 0 OnHand
    cursor.execute(
        "SELECT OnHand, OOSFlag FROM club_inventories WHERE ClubID = ? AND ItemID = ?", 
        (club_id, item_id)
    )
    res = cursor.fetchone()
    
    # Check if this member has already submitted a pending OOS report for this item at this club
    cursor.execute(
        "SELECT SignalID FROM signals WHERE MemberID = ? AND ItemID = ? AND ClubID = ? AND Status = 'Pending'",
        (member_id, item_id, club_id)
    )
    dup = cursor.fetchone()
    if dup:
        conn.close()
        msg = f"You have already reported this item as out-of-stock at this club. The report is under review."
        return Event(
            output={"status": "Rejected", "message": msg},
            state={
                "status": "Rejected",
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    if not res:
        conn.close()
        return Event(
            output={"status": "Error", "message": "Item inventory record not found."},
            state={"status": "Error", "outcome_message": "Item inventory record not found."}
        )
        
    on_hand, current_oos_flag = res
    today_str = datetime.date.today().isoformat()
    signal_id = f"S{int(datetime.datetime.now().timestamp())}{random.randint(10, 99)}"
    
    if on_hand == 0:
        # Verified Out of Stock
        # Update inventory OOS flag
        cursor.execute(
            "UPDATE club_inventories SET OOSFlag = 'Yes', LastRestocked = ? WHERE ClubID = ? AND ItemID = ?",
            (today_str, club_id, item_id)
        )
        
        # Log the signal
        cursor.execute(
            "INSERT INTO signals (SignalID, MemberID, ClubID, SignalType, ItemID, CandidateID, Status, AssignedRole, Created) "
            "VALUES (?, ?, ?, 'OOS', ?, NULL, 'Pending', 'Club Associate', ?)",
            (signal_id, member_id, club_id, item_id, today_str)
        )
        
        if is_ambassador:
            # Query reward rules for Verified OOS Report (RR001)
            cursor.execute("SELECT Points, TrustIncrease FROM reward_rules WHERE RuleID = 'RR001'")
            pts_res = cursor.fetchone()
            points = pts_res[0] if pts_res else 5
            trust = pts_res[1] if pts_res else 2
            
            # Update member score
            cursor.execute(
                "UPDATE members SET SamsPoints = SamsPoints + ?, TrustScore = TrustScore + ? WHERE MemberID = ?",
                (points, trust, member_id)
            )
            msg = f"Verified OOS Signal logged successfully. Awarded {points} points and +{trust} Trust Score."
        else:
            # Set trust score to 0 and award no points
            cursor.execute(
                "UPDATE members SET TrustScore = 0 WHERE MemberID = ?",
                (member_id,)
            )
            points = 0
            trust = 0
            msg = f"Verified OOS Signal logged (no Sam's Points awarded as you are not an Ambassador)."
            
        conn.commit()
        conn.close()
        
        return Event(
            output={"status": "Success", "message": msg},
            state={
                "status": "Success",
                "outcome_message": msg,
                "points_awarded": points,
                "trust_increase": trust
            }
        )
    else:
        # Unverified report (item has stock on hand according to database)
        cursor.execute(
            "INSERT INTO signals (SignalID, MemberID, ClubID, SignalType, ItemID, CandidateID, Status, AssignedRole, Created) "
            "VALUES (?, ?, ?, 'OOS', ?, NULL, 'Unverified', 'Club Associate', ?)",
            (signal_id, member_id, club_id, item_id, today_str)
        )
        
        if not is_ambassador:
            # Set trust score to 0
            cursor.execute(
                "UPDATE members SET TrustScore = 0 WHERE MemberID = ?",
                (member_id,)
            )
            
        conn.commit()
        conn.close()
        
        msg = f"Unverified OOS Signal: Database records show {on_hand} items on hand. Under investigation."
        return Event(
            output={"status": "Unverified", "message": msg},
            state={
                "status": "Unverified",
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )

def handle_suggestion_analysis(ctx: Context, node_input: Any) -> Event:
    """Post-processes LLM classification output and inserts product proposal in DB."""
    conn = get_db_conn()
    cursor = conn.cursor()
    
    member_id = ctx.state.get("member_id")
    description = ctx.state.get("description")
    store = ctx.state.get("store_where_found")
    reason = ctx.state.get("reason")
    
    analysis = ctx.state.get("suggestion_analysis")
    if analysis is None:
        analysis = {}
        
    if isinstance(analysis, dict):
        is_appr = bool(analysis.get("is_appropriate", True))
        dept = str(analysis.get("department", "Grocery"))
        cat = str(analysis.get("suggested_category", "General"))
        reasoning = str(analysis.get("reasoning", ""))
    else:
        is_appr = bool(getattr(analysis, "is_appropriate", True))
        dept = str(getattr(analysis, "department", "Grocery"))
        cat = str(getattr(analysis, "suggested_category", "General"))
        reasoning = str(getattr(analysis, "reasoning", ""))
    
    # Check globally (across all members) for this item description
    cursor.execute("SELECT CandidateID, ItemDescription, MemberIDProposer FROM candidate_products")
    global_proposals = cursor.fetchall()
    
    existing_candidate_id = None
    existing_dept = None
    existing_cat = None
    original_proposer = None
    
    for row in global_proposals:
        cand_id, full_desc, proposer_id = row
        if " - " in full_desc:
            parts = full_desc.split(" - ", 1)
            parsed_dept = parts[0]
            core = parts[1]
            parsed_cat = "General"
            if " (" in core:
                core_parts = core.rsplit(" (", 1)
                core = core_parts[0]
                parsed_cat = core_parts[1].rstrip(")")
            
            if core.strip().lower() == description.strip().lower():
                existing_candidate_id = cand_id
                existing_dept = parsed_dept
                existing_cat = parsed_cat
                original_proposer = proposer_id
                break
                
    if existing_candidate_id:
        if original_proposer == member_id:
            # Original proposer cannot propose it again
            conn.close()
            msg = f"Product suggestion rejected: You have already proposed '{description}'."
            return Event(
                output={"status": "Rejected", "message": msg},
                state={
                    "status": "Rejected",
                    "outcome_message": msg,
                    "is_appropriate": False,
                    "analysis_reasoning": "Duplicate suggestion by proposer."
                }
            )
        else:
            # Different member suggests it -> automatically convert to an UPVOTE!
            cursor.execute("SELECT UpVotes, Status FROM candidate_products WHERE CandidateID = ?", (existing_candidate_id,))
            cand_res = cursor.fetchone()
            upvotes, status_cp = cand_res[0], cand_res[1]
            threshold = 10
            
            new_upvotes = upvotes + 1
            new_status = status_cp
            if new_upvotes >= threshold and status_cp == "New":
                new_status = "Threshold Crossed"
            elif new_upvotes >= 5 and status_cp == "New":
                new_status = "Trending"
                
            cursor.execute(
                "UPDATE candidate_products SET UpVotes = ?, Status = ? WHERE CandidateID = ?",
                (new_upvotes, new_status, existing_candidate_id)
            )
            
            # Log Upvote signal
            signal_id = f"S{int(datetime.datetime.now().timestamp())}{random.randint(10, 99)}"
            today_str = datetime.date.today().isoformat()
            cursor.execute(
                "INSERT INTO signals (SignalID, MemberID, ClubID, SignalType, ItemID, CandidateID, Status, AssignedRole, Created) "
                "VALUES (?, ?, NULL, 'Upvote', NULL, ?, ?, 'Merchant', ?)",
                (signal_id, member_id, existing_candidate_id, new_status, today_str)
            )
            
            # Award upvote points (RR003: points = 1)
            cursor.execute("SELECT Points, TrustIncrease FROM reward_rules WHERE RuleID = 'RR003'")
            pts_res = cursor.fetchone()
            points = pts_res[0] if pts_res else 1
            trust = pts_res[1] if pts_res else 0
            
            cursor.execute(
                "UPDATE members SET SamsPoints = SamsPoints + ?, TrustScore = TrustScore + ? WHERE MemberID = ?",
                (points, trust, member_id)
            )
            
            conn.commit()
            conn.close()
            
            msg = f"Item '{description}' has already been proposed. Registered your request as an upvote for the existing candidate! Upvote count is now {new_upvotes}."
            return Event(
                output={"status": "Success", "message": msg},
                state={
                    "status": "Success",
                    "outcome_message": msg,
                    "is_appropriate": True,
                    "points_awarded": points,
                    "trust_increase": trust
                }
            )
            
    # Set to matching values if consistency override was loaded
    if existing_dept and existing_cat:
        dept = existing_dept
        cat = existing_cat
        reasoning = f"Classification overridden to match existing proposal for this item (Dept: {dept}, Cat: {cat}). Original reasoning: {reasoning}"

    today_str = datetime.date.today().isoformat()
    candidate_id = f"P{int(datetime.datetime.now().timestamp())}{random.randint(10, 99)}"
    signal_id = f"S{int(datetime.datetime.now().timestamp())}{random.randint(10, 99)}"
    
    if not is_appr:
        conn.close()
        msg = f"Product suggestion rejected: Flagged as inappropriate. Reasoning: {reasoning}"
        return Event(
            output={"status": "Rejected", "message": msg},
            state={
                "status": "Rejected",
                "outcome_message": msg,
                "is_appropriate": False,
                "analysis_reasoning": reasoning
            }
        )
        
    # Insert new candidate product
    cursor.execute(
        "INSERT INTO candidate_products (CandidateID, ItemDescription, PhotoURL, StoreWhereFound, MemberIDProposer, ProposalDate, UpVotes, Status, Threshold) "
        "VALUES (?, ?, NULL, ?, ?, ?, 1, 'New', 10)",
        (candidate_id, f"{dept} - {description} ({cat})", store, member_id, today_str)
    )
    
    # Log signal
    cursor.execute(
        "INSERT INTO signals (SignalID, MemberID, ClubID, SignalType, ItemID, CandidateID, Status, AssignedRole, Created) "
        "VALUES (?, ?, NULL, 'ProductSuggestion', NULL, ?, 'New', 'Merchant', ?)",
        (signal_id, member_id, candidate_id, today_str)
    )
    
    # Award proposal points (Rule RR002: points = 1)
    cursor.execute("SELECT Points, TrustIncrease FROM reward_rules WHERE RuleID = 'RR002'")
    pts_res = cursor.fetchone()
    points = pts_res[0] if pts_res else 1
    trust = pts_res[1] if pts_res else 0
    
    cursor.execute(
        "UPDATE members SET SamsPoints = SamsPoints + ?, TrustScore = TrustScore + ? WHERE MemberID = ?",
        (points, trust, member_id)
    )
    
    conn.commit()
    conn.close()
    
    msg = f"Product proposal '{description}' submitted in department '{dept}'. Awarded {points} points."
    return Event(
        output={"status": "Success", "message": msg},
        state={
            "status": "Success",
            "outcome_message": msg,
            "is_appropriate": True,
            "department": dept,
            "suggested_category": cat,
            "analysis_reasoning": reasoning,
            "points_awarded": points,
            "trust_increase": trust
        }
    )

def process_upvote(ctx: Context, node_input: SignalInput) -> Event:
    """Processes an upvote for a candidate product, handles threshold crossings."""
    conn = get_db_conn()
    cursor = conn.cursor()
    
    member_id = ctx.state.get("member_id")
    candidate_id = ctx.state.get("candidate_id")
    today_str = datetime.date.today().isoformat()
    
    cursor.execute(
        "SELECT ItemDescription, MemberIDProposer, UpVotes, Threshold, Status FROM candidate_products WHERE CandidateID = ?",
        (candidate_id,)
    )
    res = cursor.fetchone()
    
    if not res:
        conn.close()
        return Event(
            output={"status": "Error", "message": "Candidate product record not found."},
            state={"status": "Error", "outcome_message": "Candidate product record not found."}
        )
        
    desc, proposer_id, upvotes, threshold, status = res
    new_upvotes = upvotes + 1
    
    cursor.execute(
        "UPDATE candidate_products SET UpVotes = ? WHERE CandidateID = ?",
        (new_upvotes, candidate_id)
    )
    
    points_awarded = 0
    trust_awarded = 0
    msg = f"Upvoted successfully. Total upvotes: {new_upvotes}."
    
    if new_upvotes >= threshold and status == "New":
        cursor.execute(
            "UPDATE candidate_products SET Status = 'Threshold Crossed' WHERE CandidateID = ?",
            (candidate_id,)
        )
        
        signal_id = f"S{int(datetime.datetime.now().timestamp())}{random.randint(10, 99)}"
        cursor.execute(
            "INSERT INTO signals (SignalID, MemberID, ClubID, SignalType, ItemID, CandidateID, Status, AssignedRole, Created) "
            "VALUES (?, ?, NULL, 'ProductSuggestion', NULL, ?, 'Merchant Review', 'Merchant', ?)",
            (signal_id, proposer_id, candidate_id, today_str)
        )
        
        cursor.execute("SELECT Points, TrustIncrease FROM reward_rules WHERE RuleID = 'RR003'")
        pts_res = cursor.fetchone()
        points_awarded = pts_res[0] if pts_res else 20
        trust_awarded = pts_res[1] if pts_res else 5
        
        cursor.execute(
            "UPDATE members SET SamsPoints = SamsPoints + ?, TrustScore = TrustScore + ? WHERE MemberID = ?",
            (points_awarded, trust_awarded, proposer_id)
        )
        
        msg = f"Upvoted! Threshold Crossed ({new_upvotes}/{threshold}). Proposer {proposer_id} awarded {points_awarded} points & +{trust_awarded} Trust."
        
    conn.commit()
    conn.close()
    
    return Event(
        output={"status": "Success", "message": msg},
        state={
            "status": "Success",
            "outcome_message": msg,
            "points_awarded": points_awarded,
            "trust_increase": trust_awarded
        }
    )

def process_associate_action(ctx: Context, node_input: SignalInput) -> Event:
    """Processes verification and stock actions submitted by club associates."""
    conn = get_db_conn()
    cursor = conn.cursor()
    
    signal_id = ctx.state.get("signal_id")
    action = ctx.state.get("action")
    today_str = datetime.date.today().isoformat()
    
    # 1. Fetch signal details
    cursor.execute(
        "SELECT MemberID, ClubID, ItemID, Status, AssignedRole FROM signals WHERE SignalID = ?",
        (signal_id,)
    )
    res = cursor.fetchone()
    if not res:
        conn.close()
        return Event(
            output={"status": "Error", "message": f"Signal {signal_id} not found in database."},
            state={"status": "Error", "outcome_message": f"Signal {signal_id} not found."}
        )
        
    member_id, club_id, item_id, current_status, assigned_role = res
    
    # 2. Check if the task is already closed
    if current_status == "Closed" or current_status.startswith("Closed"):
        conn.close()
        return Event(
            output={"status": "Error", "message": f"Signal {signal_id} is already closed."},
            state={"status": "Error", "outcome_message": f"Signal {signal_id} is already closed."}
        )
        
    if action == "false_alarm":
        # Decrease member trust score by 10 points (minimum 0), award 0 Sam's Points
        cursor.execute("SELECT TrustScore FROM members WHERE MemberID = ?", (member_id,))
        m_res = cursor.fetchone()
        new_trust = 0
        if m_res:
            current_trust = m_res[0]
            new_trust = max(0, current_trust - 10)
            cursor.execute("UPDATE members SET TrustScore = ? WHERE MemberID = ?", (new_trust, member_id))
            
        # Set signal OOS flag in inventory to 'No' (since item was actually available!)
        cursor.execute(
            "UPDATE club_inventories SET OOSFlag = 'No' WHERE ClubID = ? AND ItemID = ?",
            (club_id, item_id)
        )
        
        # Close the task
        cursor.execute(
            "UPDATE signals SET Status = 'Closed - False Alarm' WHERE SignalID = ?",
            (signal_id,)
        )
        conn.commit()
        conn.close()
        
        msg = f"Signal S{signal_id} marked as False Alarm. Member {member_id} trust score penalized by 10 points (New Trust: {new_trust}%)."
        return Event(
            output={"status": "Closed - False Alarm", "message": msg},
            state={
                "status": "Closed - False Alarm",
                "outcome_message": msg,
                "trust_increase": -10,
                "points_awarded": 0
            }
        )
        
    elif action == "restock":
        # Fetch inventory details
        cursor.execute(
            "SELECT OnHand, BackRoom, LostSalesToday FROM club_inventories WHERE ClubID = ? AND ItemID = ?",
            (club_id, item_id)
        )
        inv_res = cursor.fetchone()
        if not inv_res:
            conn.close()
            return Event(
                output={"status": "Error", "message": "Inventory record not found."},
                state={"status": "Error", "outcome_message": "Inventory record not found."}
            )
            
        on_hand, back_room, lost_sales = inv_res
        
        # Restock shelf using backroom stock
        new_on_hand = on_hand + back_room
        new_back_room = 0
        new_lost_sales = lost_sales + 1
        
        cursor.execute(
            "UPDATE club_inventories SET OnHand = ?, BackRoom = ?, LostSalesToday = ?, OOSFlag = 'No', LastRestocked = ? "
            "WHERE ClubID = ? AND ItemID = ?",
            (new_on_hand, new_back_room, new_lost_sales, today_str, club_id, item_id)
        )
        
        # Award member: 10 Sam's Points and +5 Trust Score
        cursor.execute(
            "UPDATE members SET SamsPoints = SamsPoints + 10, TrustScore = MIN(100, TrustScore + 5) WHERE MemberID = ?",
            (member_id,)
        )
        
        # Close signal
        cursor.execute(
            "UPDATE signals SET Status = 'Closed - Restocked' WHERE SignalID = ?",
            (signal_id,)
        )
        conn.commit()
        conn.close()
        
        msg = f"OOS Signal S{signal_id} verified and shelf restocked from backroom. Member {member_id} awarded 10 points and +5 trust."
        return Event(
            output={"status": "Closed - Restocked", "message": msg},
            state={
                "status": "Closed - Restocked",
                "outcome_message": msg,
                "points_awarded": 10,
                "trust_increase": 5
            }
        )
        
    elif action == "verified_oos":
        # Increment lost sales
        cursor.execute(
            "SELECT LostSalesToday FROM club_inventories WHERE ClubID = ? AND ItemID = ?",
            (club_id, item_id)
        )
        inv_res = cursor.fetchone()
        lost_sales = inv_res[0] if inv_res else 0
        new_lost_sales = lost_sales + 1
        
        cursor.execute(
            "UPDATE club_inventories SET LostSalesToday = ? WHERE ClubID = ? AND ItemID = ?",
            (new_lost_sales, club_id, item_id)
        )
        
        # Check if LostSalesToday >= 3 (threshold crossed)
        if new_lost_sales >= 3:
            # Route to Inventory Associate
            cursor.execute(
                "UPDATE signals SET AssignedRole = 'Inventory Associate', Status = 'Pending' WHERE SignalID = ?",
                (signal_id,)
            )
            msg = f"OOS Signal S{signal_id} verified. Shelf & backroom are empty. Lost sales today = {new_lost_sales}. Escalated to Inventory Associate."
            status_out = "Escalated"
        else:
            # Close task as verified OOS
            cursor.execute(
                "UPDATE signals SET Status = 'Closed - Verified OOS' WHERE SignalID = ?",
                (signal_id,)
            )
            msg = f"OOS Signal S{signal_id} verified. Shelf & backroom are empty. Lost sales today = {new_lost_sales}. Task closed."
            status_out = "Closed - Verified OOS"
            
        conn.commit()
        conn.close()
        
        return Event(
            output={"status": status_out, "message": msg},
            state={
                "status": status_out,
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    else:
        conn.close()
        return Event(
            output={"status": "Error", "message": f"Unknown associate action: {action}"},
            state={"status": "Error", "outcome_message": f"Unknown associate action: {action}"}
        )

def handle_voice_analysis(ctx: Context, node_input: Any) -> Event:
    """Processes the voice classification output, matching products and routing accordingly."""
    conn = get_db_conn()
    cursor = conn.cursor()
    
    member_id = ctx.state.get("member_id")
    club_id = ctx.state.get("club_id") or "C100"
    
    # Check if member is an Ambassador
    cursor.execute("SELECT Ambassador FROM members WHERE MemberID = ?", (member_id,))
    m_row = cursor.fetchone()
    is_ambassador = m_row and m_row[0] == "Yes"
    
    analysis = ctx.state.get("voice_analysis")
    if analysis is None:
        analysis = {}
        
    if isinstance(analysis, dict):
        intent = str(analysis.get("intent", "OOS"))
        extracted_item = str(analysis.get("extracted_item", ""))
        is_negative = bool(analysis.get("is_negative_sentiment", True))
        explanation = str(analysis.get("explanation", ""))
    else:
        intent = str(getattr(analysis, "intent", "OOS"))
        extracted_item = str(getattr(analysis, "extracted_item", ""))
        is_negative = bool(getattr(analysis, "is_negative_sentiment", True))
        explanation = str(getattr(analysis, "explanation", ""))
        
    if not extracted_item:
        conn.close()
        msg = "Voice input received but no item could be identified."
        return Event(
            output={"status": "Rejected", "message": msg},
            state={"status": "Rejected", "outcome_message": msg}
        )
        
    if intent == "ProductSuggestion" or (intent == "OOS" and not is_negative):
        # Route to ProductSuggestion flow
        ctx.state["description"] = extracted_item
        ctx.state["suggestion_analysis"] = {
            "is_appropriate": True,
            "department": "Produce" if "banana" in extracted_item.lower() or "kimchi" in extracted_item.lower() else "Grocery",
            "suggested_category": "Trending Items",
            "reasoning": explanation
        }
        conn.close()
        return handle_suggestion_analysis(ctx, node_input)
        
    # Otherwise, treat as OOS
    item_name = extracted_item.strip().lower()
    cursor.execute("SELECT ItemID, ItemDescription FROM items")
    all_items = cursor.fetchall()
    
    matched_item_id = None
    matched_item_desc = None
    
    # Check for exact or partial word overlaps
    for item_id_db, item_desc_db in all_items:
        desc_lower = item_desc_db.lower()
        if item_name in desc_lower or desc_lower in item_name:
            matched_item_id = item_id_db
            matched_item_desc = item_desc_db
            break
            
    # Fallback word-by-word
    if not matched_item_id:
        item_words = set(item_name.split())
        for item_id_db, item_desc_db in all_items:
            desc_words = set(item_desc_db.lower().split())
            matching_words = item_words.intersection(desc_words)
            if any(len(w) > 3 for w in matching_words):
                matched_item_id = item_id_db
                matched_item_desc = item_desc_db
                break
                
    if not matched_item_id:
        conn.close()
        msg = f"Voice report processed. Detected out-of-stock item '{extracted_item}', but it does not match our catalog."
        return Event(
            output={"status": "Unmatched", "message": msg},
            state={"status": "Unmatched", "outcome_message": msg}
        )
        
    # Execute process_oos database operations on the matched item
    cursor.execute(
        "SELECT OnHand, OOSFlag FROM club_inventories WHERE ClubID = ? AND ItemID = ?", 
        (club_id, matched_item_id)
    )
    res = cursor.fetchone()
    
    # Check duplicates
    cursor.execute(
        "SELECT SignalID FROM signals WHERE MemberID = ? AND ItemID = ? AND ClubID = ? AND Status = 'Pending'",
        (member_id, matched_item_id, club_id)
    )
    dup = cursor.fetchone()
    if dup:
        conn.close()
        msg = f"Duplicate report: OOS for '{matched_item_desc}' at this club is already under review."
        return Event(
            output={"status": "Rejected", "message": msg},
            state={"status": "Rejected", "outcome_message": msg}
        )
        
    on_hand = res[0] if res else 0
    today_str = datetime.date.today().isoformat()
    signal_id = f"S{int(datetime.datetime.now().timestamp())}{random.randint(10, 99)}"
    
    if on_hand == 0:
        cursor.execute(
            "UPDATE club_inventories SET OOSFlag = 'Yes', LastRestocked = ? WHERE ClubID = ? AND ItemID = ?",
            (today_str, club_id, matched_item_id)
        )
        cursor.execute(
            "INSERT INTO signals (SignalID, MemberID, ClubID, SignalType, ItemID, CandidateID, Status, AssignedRole, Created) "
            "VALUES (?, ?, ?, 'OOS', ?, NULL, 'Pending', 'Club Associate', ?)",
            (signal_id, member_id, club_id, matched_item_id, today_str)
        )
        
        if is_ambassador:
            cursor.execute("SELECT Points, TrustIncrease FROM reward_rules WHERE RuleID = 'RR001'")
            pts_res = cursor.fetchone()
            points = pts_res[0] if pts_res else 5
            trust = pts_res[1] if pts_res else 2
            cursor.execute(
                "UPDATE members SET SamsPoints = SamsPoints + ?, TrustScore = TrustScore + ? WHERE MemberID = ?",
                (points, trust, member_id)
            )
            msg = f"Voice OOS report logged for {matched_item_desc} ({matched_item_id}). Awarded {points} points and +{trust} Trust."
        else:
            cursor.execute(
                "UPDATE members SET TrustScore = 0 WHERE MemberID = ?",
                (member_id,)
            )
            points = 0
            trust = 0
            msg = f"Voice OOS report logged for {matched_item_desc} ({matched_item_id}) (no Sam's Points awarded as you are not an Ambassador)."
            
        conn.commit()
        conn.close()
        
        return Event(
            output={"status": "Success", "message": msg},
            state={
                "status": "Success",
                "outcome_message": msg,
                "points_awarded": points,
                "trust_increase": trust
            }
        )
    else:
        cursor.execute(
            "INSERT INTO signals (SignalID, MemberID, ClubID, SignalType, ItemID, CandidateID, Status, AssignedRole, Created) "
            "VALUES (?, ?, ?, 'OOS', ?, NULL, 'Unverified', 'Club Associate', ?)",
            (signal_id, member_id, club_id, matched_item_id, today_str)
        )
        
        if not is_ambassador:
            cursor.execute(
                "UPDATE members SET TrustScore = 0 WHERE MemberID = ?",
                (member_id,)
            )
            
        conn.commit()
        conn.close()
        msg = f"Voice OOS report logged for {matched_item_desc} ({matched_item_id}). Database shows stock is on hand (Unverified)."
        return Event(
            output={"status": "Unverified", "message": msg},
            state={
                "status": "Unverified",
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )

def process_merchant_action(ctx: Context, node_input: SignalInput) -> Event:
    """Processes product suggestions and threshold review tasks verified by merchants."""
    conn = get_db_conn()
    cursor = conn.cursor()
    
    signal_id = ctx.state.get("signal_id")
    candidate_id = ctx.state.get("candidate_id")
    action = ctx.state.get("action")
    next_threshold = ctx.state.get("next_threshold")
    archive_reason = ctx.state.get("archive_reason")
    
    # 1. Verify if the signal exists
    cursor.execute(
        "SELECT Status, AssignedRole FROM signals WHERE SignalID = ?",
        (signal_id,)
    )
    res = cursor.fetchone()
    if not res:
        conn.close()
        return Event(
            output={"status": "Error", "message": f"Signal {signal_id} not found in database."},
            state={"status": "Error", "outcome_message": f"Signal {signal_id} not found."}
        )
        
    current_status, assigned_role = res
    if current_status.startswith("Closed"):
        conn.close()
        return Event(
            output={"status": "Error", "message": f"Signal {signal_id} is already closed."},
            state={"status": "Error", "outcome_message": f"Signal {signal_id} is already closed."}
        )
        
    if action == "explore":
        # Move to exploration
        new_status = "Exploration"
        thresh = next_threshold if next_threshold is not None else 20
        cursor.execute(
            "UPDATE candidate_products SET Status = ?, Threshold = ? WHERE CandidateID = ?",
            (new_status, thresh, candidate_id)
        )
        cursor.execute(
            "UPDATE signals SET Status = 'Closed - Exploration' WHERE SignalID = ?",
            (signal_id,)
        )
        conn.commit()
        conn.close()
        
        msg = f"Candidate product {candidate_id} moved to Exploration. Next review threshold set to {thresh} upvotes."
        return Event(
            output={"status": "Closed - Exploration", "message": msg},
            state={
                "status": "Closed - Exploration",
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    elif action == "approve_launch":
        # Approve Launch
        new_status = "Launch Approved"
        cursor.execute(
            "UPDATE candidate_products SET Status = ? WHERE CandidateID = ?",
            (new_status, candidate_id)
        )
        cursor.execute(
            "UPDATE signals SET Status = 'Closed - Launch Approved' WHERE SignalID = ?",
            (signal_id,)
        )
        conn.commit()
        conn.close()
        
        msg = f"Candidate product {candidate_id} launch approved successfully!"
        return Event(
            output={"status": "Closed - Launch Approved", "message": msg},
            state={
                "status": "Closed - Launch Approved",
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    elif action == "increase_threshold":
        # Increase threshold and return to trending
        new_status = "New"
        thresh = next_threshold if next_threshold is not None else 20
        cursor.execute(
            "UPDATE candidate_products SET Status = ?, Threshold = ? WHERE CandidateID = ?",
            (new_status, thresh, candidate_id)
        )
        cursor.execute(
            "UPDATE signals SET Status = 'Closed - Threshold Increased' WHERE SignalID = ?",
            (signal_id,)
        )
        conn.commit()
        conn.close()
        
        msg = f"Candidate product {candidate_id} returned to queue with increased threshold of {thresh} upvotes."
        return Event(
            output={"status": "Closed - Threshold Increased", "message": msg},
            state={
                "status": "Closed - Threshold Increased",
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    elif action == "archive":
        # Reject/archive product proposal
        new_status = "Archived"
        cursor.execute(
            "UPDATE candidate_products SET Status = ? WHERE CandidateID = ?",
            (new_status, candidate_id)
        )
        cursor.execute(
            "UPDATE signals SET Status = 'Closed - Archived' WHERE SignalID = ?",
            (signal_id,)
        )
        conn.commit()
        conn.close()
        
        reason_txt = f" Reason: {archive_reason}" if archive_reason else ""
        msg = f"Candidate product {candidate_id} rejected and archived.{reason_txt}"
        return Event(
            output={"status": "Closed - Archived", "message": msg},
            state={
                "status": "Closed - Archived",
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    else:
        conn.close()
        return Event(
            output={"status": "Error", "message": f"Unknown merchant action: {action}"},
            state={"status": "Error", "outcome_message": f"Unknown merchant action: {action}"}
        )

def process_inventory_action(ctx: Context, node_input: SignalInput) -> Event:
    """Processes replenishment and inventory exception tasks verified by inventory associates."""
    conn = get_db_conn()
    cursor = conn.cursor()
    
    signal_id = ctx.state.get("signal_id")
    item_id = ctx.state.get("item_id")
    club_id = ctx.state.get("club_id")
    action = ctx.state.get("action")
    res_plan = ctx.state.get("resolution_plan")
    fore_adj = ctx.state.get("forecast_adjustment")
    tf_source = ctx.state.get("transfer_source")
    sup_const = ctx.state.get("supply_constraint")
    
    # 1. Verify if the signal exists
    cursor.execute(
        "SELECT Status, AssignedRole FROM signals WHERE SignalID = ?",
        (signal_id,)
    )
    res = cursor.fetchone()
    if not res:
        conn.close()
        return Event(
            output={"status": "Error", "message": f"Signal {signal_id} not found in database."},
            state={"status": "Error", "outcome_message": f"Signal {signal_id} not found."}
        )
        
    current_status, assigned_role = res
    if current_status.startswith("Closed"):
        conn.close()
        return Event(
            output={"status": "Error", "message": f"Signal {signal_id} is already closed."},
            state={"status": "Error", "outcome_message": f"Signal {signal_id} is already closed."}
        )
        
    # Reset LostSalesToday in club_inventories when resolving
    if action in ("expedite", "adjust_forecast", "transfer", "vendor_issue"):
        cursor.execute(
            "UPDATE club_inventories SET LostSalesToday = 0 WHERE ClubID = ? AND ItemID = ?",
            (club_id, item_id)
        )
        
    if action == "expedite":
        new_status = "Closed - Replenishment Expedited"
        cursor.execute(
            "UPDATE signals SET Status = ? WHERE SignalID = ?",
            (new_status, signal_id)
        )
        conn.commit()
        conn.close()
        
        msg = f"Replenishment expedited. Resolution plan: {res_plan or 'None'}"
        return Event(
            output={"status": new_status, "message": msg},
            state={
                "status": new_status,
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    elif action == "adjust_forecast":
        new_status = "Closed - Forecast Adjusted"
        cursor.execute(
            "UPDATE signals SET Status = ? WHERE SignalID = ?",
            (new_status, signal_id)
        )
        conn.commit()
        conn.close()
        
        msg = f"Forecast adjusted by +{fore_adj or '20%'}. Reorder signal triggered."
        return Event(
            output={"status": new_status, "message": msg},
            state={
                "status": new_status,
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    elif action == "transfer":
        new_status = "Closed - Transfer Requested"
        cursor.execute(
            "UPDATE signals SET Status = ? WHERE SignalID = ?",
            (new_status, signal_id)
        )
        conn.commit()
        conn.close()
        
        msg = f"Inventory transfer requested from: {tf_source or 'Nearby Club/DC'}"
        return Event(
            output={"status": new_status, "message": msg},
            state={
                "status": new_status,
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    elif action == "vendor_issue":
        new_status = "Closed - Supply Constraint Flagged"
        cursor.execute(
            "UPDATE signals SET Status = ? WHERE SignalID = ?",
            (new_status, signal_id)
        )
        conn.commit()
        conn.close()
        
        msg = f"Supply constraint flagged. Issue: {sup_const or 'Vendor Constraint'}"
        return Event(
            output={"status": new_status, "message": msg},
            state={
                "status": new_status,
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    elif action == "monitor":
        new_status = "Closed - Monitoring"
        cursor.execute(
            "UPDATE signals SET Status = ? WHERE SignalID = ?",
            (new_status, signal_id)
        )
        conn.commit()
        conn.close()
        
        msg = "Monitor status set. SLA tracking timer initiated."
        return Event(
            output={"status": new_status, "message": msg},
            state={
                "status": new_status,
                "outcome_message": msg,
                "points_awarded": 0,
                "trust_increase": 0
            }
        )
        
    else:
        conn.close()
        return Event(
            output={"status": "Error", "message": f"Unknown inventory action: {action}"},
            state={"status": "Error", "outcome_message": f"Unknown inventory action: {action}"}
        )

def assemble_outcome(ctx: Context, node_input: dict) -> SignalOutput:
    """Assembles the final structured output from workflow state."""
    state = ctx.state
    return SignalOutput(
        signal_type=str(state.get("signal_type") or "Unknown"),
        status=str(state.get("status") or "Completed"),
        message=str(state.get("outcome_message") or "Signal processed."),
        points_awarded=int(state.get("points_awarded") or 0),
        trust_increase=int(state.get("trust_increase") or 0)
    )

# ------------------------------------------------------------------------------
# 5. Workflow Graph Definition & Application
# ------------------------------------------------------------------------------

root_agent = Workflow(
    name="root_agent",
    description="SignalSense Enterprise backend router and decision processor.",
    input_schema=SignalInput,
    output_schema=SignalOutput,
    state_schema=SignalState,
    edges=[
        ('START', extract_signal),
        (extract_signal, {
            "OOS": process_oos,
            "ProductSuggestion": suggestion_analyzer,
            "Upvote": process_upvote,
            "AssociateAction": process_associate_action,
            "MerchantAction": process_merchant_action,
            "InventoryAction": process_inventory_action,
            "VoiceSignal": voice_classifier
        }),
        (suggestion_analyzer, handle_suggestion_analysis),
        (voice_classifier, handle_voice_analysis),
        (process_oos, assemble_outcome),
        (handle_suggestion_analysis, assemble_outcome),
        (handle_voice_analysis, assemble_outcome),
        (process_upvote, assemble_outcome),
        (process_associate_action, assemble_outcome),
        (process_merchant_action, assemble_outcome),
        (process_inventory_action, assemble_outcome)
    ]
)

app = App(name="signalsense_agent", root_agent=root_agent)
