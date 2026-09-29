from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SELECT = "select"
    EXTRACT = "extract"
    WAIT_FOR = "wait_for"


class RiskLevel(str, Enum):
    SAFE = "safe"                
    LOW = "low"                
    HIGH = "high"                


class LocatorStrategy(str, Enum):
    ROLE_NAME = "role_name"     
    TEXT = "text"               
    LABEL = "label"              
    CSS = "css"                 
    XPATH = "xpath"              


class Locator(BaseModel):
    strategy: LocatorStrategy
    value: str
    role: Optional[str] = None        
    frame_path: list[str] = Field(default_factory=list)  
    exact: bool = False


class ResolvedTarget(BaseModel):
    """A target with a primary locator and an ordered fallback chain."""
    description: str                    
    primary: Locator
    fallbacks: list[Locator] = Field(default_factory=list)


class CheckpointAssertion(str, Enum):
    URL_MATCHES = "url_matches"
    TEXT_PRESENT = "text_present"
    TEXT_ABSENT = "text_absent"
    ELEMENT_VISIBLE = "element_visible"
    STATUS_CODE = "status_code"


class Checkpoint(BaseModel):
    assertion: CheckpointAssertion
    value: str                          
    target: Optional[ResolvedTarget] = None  


class ArtifactStep(BaseModel):
    step_id: str
    action: ActionType
    target: Optional[ResolvedTarget] = None     
    value_template: Optional[str] = None          
    output_key: Optional[str] = None               
    risk: RiskLevel = RiskLevel.SAFE
    checkpoint: Optional[Checkpoint] = None         
    max_wait_ms: int = 8000
    retry_on_timeout: int = 1                       


class ParamSpec(BaseModel):
    name: str
    type: str                
    required: bool = True
    description: str = ""
    sensitive: bool = False  


class OutputSpec(BaseModel):
    name: str
    type: str
    description: str = ""


class KnownOutcome(BaseModel):
    outcome_id: str                    
    match_assertion: CheckpointAssertion
    match_value: str
    description: str
    is_success: bool = False            


class Artifact(BaseModel):
    """A saved, replayable capability."""
    artifact_id: str
    name: str
    version: int = 1
    description: str

    target_app: str                      
    entry_url: str                     

    input_schema: list[ParamSpec] = Field(default_factory=list)
    output_schema: list[OutputSpec] = Field(default_factory=list)

    steps: list[ArtifactStep]
    success_checkpoint: Checkpoint
    known_outcomes: list[KnownOutcome] = Field(default_factory=list)

    created_from_run_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    review_status: str = "draft"         

    model_config = {"use_enum_values": True}
