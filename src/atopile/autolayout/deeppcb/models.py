"""Pydantic models for the DeepPCB V1 API surface."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BoardStatus(StrEnum):
    PENDING = "Pending"
    RUNNING = "Running"
    DONE = "Done"
    FAILED = "Failed"
    STOPPED = "Stopped"
    STARTING = "Starting"
    RECEIVING_REVISIONS = "ReceivingRevisions"
    STOP_REQUESTED = "StopRequested"
    STOP_FAILED = "StopFailed"


class BoardInputType(StrEnum):
    DSN = "Dsn"
    ZUKEN = "Zuken"
    KICAD = "Kicad"
    JSON = "Json"
    ALTIUM = "Altium"


class RoutingType(StrEnum):
    EMPTY_BOARD = "EmptyBoard"
    CURRENT_PROTECTED_WIRING = "CurrentProtectedWiring"
    CURRENT_UNPROTECTED_WIRING = "CurrentUnprotectedWiring"


class JobType(StrEnum):
    ROUTING = "Routing"
    PLACEMENT = "Placement"
    IDLE = "Idle"


class PricingStatus(StrEnum):
    FREE = "Free"
    PENDING = "Pending"
    PAID = "Paid"


class WorkflowReasonCode(StrEnum):
    EMPTY = "EMPTY"
    RESOURCES_UNAVAILABLE = "BOARD_START_FAILED_BY_RESOURCES_UNAVAILABLE"
    CANNOT_RESUME = "BOARD_CAN_NOT_BE_RESUMED"
    STOPPED_BY_USER = "BOARD_STOPPED_BY_USER_BEFORE_TIMEOUT"
    STOPPED_BY_TIMEOUT = "BOARD_STOPPED_BY_TIMEOUT_WITHOUT_REVISIONS"
    EXPIRED_TTL = "BOARD_START_FAILED_BY_EXPIRED_TTL"
    INTERNAL_ERROR = "BOARD_FAILED_BY_INTERNAL_ERROR"
    JOB_NOT_FOUND = "BOARD_STOP_FAILED_BY_JOB_NOT_FOUND"
    STOPPED_BY_DEPLOYMENT = "BOARD_STOPPED_BY_DEPLOYMENT"


# These fields are documented as integers in the OpenAPI spec but the live API
# returns string labels (e.g. "Standard", "Json", "ReadyToStart", "Ray", "User",
# "Api").  We type them as str | int to accept both forms.


# ---------------------------------------------------------------------------
# Shared / nested models
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class RequestIdDto(_Base):
    """RequestId can be returned as a plain string or as {\"value\": \"...\"}."""

    value: str | None = None


class ResultDto(_Base):
    """Generic API result / error envelope."""

    status_code: int | None = Field(None, alias="statusCode")
    status: str | None = None
    error_code: str | None = Field(None, alias="errorCode")
    error_message: str | None = Field(None, alias="errorMessage")


class ProblemDetails(_Base):
    type: str | None = None
    title: str | None = None
    status: int | None = None
    detail: str | None = None
    instance: str | None = None


class ResolutionDto(_Base):
    unit: str | None = None
    value: int | None = None


class PointDto(_Base):
    x: float | None = None
    y: float | None = None


class RoutingAreasDto(_Base):
    areas: list[list[PointDto]] | None = None
    flexible: bool | None = None


class BoardAnomalyDataDto(_Base):
    component_definition_id: str | None = Field(None, alias="componentDefinitionId")
    layer: int | None = None
    net_id: str | None = Field(None, alias="netId")
    net_ids: list[str] | None = Field(None, alias="netIds")
    pin_definition_id: str | None = Field(None, alias="pinDefinitionId")
    pin_id: str | None = Field(None, alias="pinId")
    pin_ids: list[str] | None = Field(None, alias="pinIds")


class BoardAnomalyDto(_Base):
    code: str | None = None
    message: str | None = None
    severity: str | None = None
    data: BoardAnomalyDataDto | None = None


class BoardRatingsAndFeedbackDto(_Base):
    speed_rating: int | None = Field(None, alias="speedRating")
    drc_rating: int | None = Field(None, alias="drcRating")
    solution_rating: int | None = Field(None, alias="solutionRating")
    average: int | None = None
    feedback: str | None = None


class RevisionRoutingStatsDto(_Base):
    num_connections: int | None = Field(None, alias="numConnections")
    num_connections_missing: int | None = Field(None, alias="numConnectionsMissing")
    num_nets_completed: int | None = Field(None, alias="numNetsCompleted")
    num_nets: int | None = Field(None, alias="numNets")
    differential_pair_num_connections: int | None = Field(
        None, alias="differentialPairNumConnections"
    )
    differential_pair_num_connections_completed: int | None = Field(
        None, alias="differentialPairNumConnectionsCompleted"
    )
    differential_pair_num_connections_missing: int | None = Field(
        None, alias="differentialPairNumConnectionsMissing"
    )
    differential_pair_num_nets: int | None = Field(
        None, alias="differentialPairNumNets"
    )
    differential_pair_num_nets_completed: int | None = Field(
        None, alias="differentialPairNumNetsCompleted"
    )
    num_plane_parts: int | None = Field(None, alias="numPlaneParts")
    num_planes: int | None = Field(None, alias="numPlanes")
    num_plane_connections: int | None = Field(None, alias="numPlaneConnections")
    num_plane_cuts: int | None = Field(None, alias="numPlaneCuts")
    plane_areas_total: int | None = Field(None, alias="planeAreasTotal")
    num_vias: int | None = Field(None, alias="numVias")
    num_vias_to_planes: int | None = Field(None, alias="numViasToPlanes")
    num_vias_at_smd: int | None = Field(None, alias="numViasAtSmd")
    differential_pair_num_vias: int | None = Field(
        None, alias="differentialPairNumVias"
    )
    num_change_layer_vias: int | None = Field(None, alias="numChangeLayerVias")
    wire_length: float | None = Field(None, alias="wireLength")
    differential_pair_wire_length: float | None = Field(
        None, alias="differentialPairWireLength"
    )


class RevisionPlacementStatsDto(_Base):
    num_components_total: int | None = None
    num_components_placed: int | None = None
    num_components_protected: int | None = None
    fully_placed: bool | None = None
    ratline_intersection: int | None = None
    fitness_score: float | None = None
    constraint_ratline_distance: int | None = None
    ratline_distance: int | None = None


# BaseStatsDto is oneOf routing or placement stats — we use a union.
BaseStatsDto = RevisionRoutingStatsDto | RevisionPlacementStatsDto


class RevisionResultDto(_Base):
    air_wires_connected: int | None = Field(None, alias="airWiresConnected")
    air_wires_not_connected: int | None = Field(None, alias="airWiresNotConnected")
    total_air_wires: int | None = Field(None, alias="totalAirWires")
    nets_connected: int | None = Field(None, alias="netsConnected")
    via_added: int | None = Field(None, alias="viaAdded")
    total_wire_length: float | None = Field(None, alias="totalWireLength")
    total_components: int | None = Field(None, alias="totalComponents")
    placed_components: int | None = Field(None, alias="placedComponents")
    protected_components: int | None = Field(None, alias="protectedComponents")
    is_fully_placed: bool | None = Field(None, alias="isFullyPlaced")
    ratline_intersection: float | None = Field(None, alias="ratlineIntersection")
    constraint_ratline_distance: float | None = Field(
        None, alias="constraintRatlineDistance"
    )
    ratline_distance: float | None = Field(None, alias="ratlineDistance")
    ratline_intersection_distance: float | None = Field(
        None, alias="ratlineIntersectionDistance"
    )
    fitness_score: float | None = Field(None, alias="fitnessScore")
    num_nets: int | None = Field(None, alias="numNets")
    num_nets_completed: int | None = Field(None, alias="numNetsCompleted")
    differential_pair_num_connections: int | None = Field(
        None, alias="differentialPairNumConnections"
    )
    differential_pair_num_connections_completed: int | None = Field(
        None, alias="differentialPairNumConnectionsCompleted"
    )
    differential_pair_num_connections_missing: int | None = Field(
        None, alias="differentialPairNumConnectionsMissing"
    )
    differential_pair_num_nets: int | None = Field(
        None, alias="differentialPairNumNets"
    )
    differential_pair_num_nets_completed: int | None = Field(
        None, alias="differentialPairNumNetsCompleted"
    )
    num_plane_parts: int | None = Field(None, alias="numPlaneParts")
    num_planes: int | None = Field(None, alias="numPlanes")
    num_plane_connections: int | None = Field(None, alias="numPlaneConnections")
    num_plane_cuts: int | None = Field(None, alias="numPlaneCuts")
    plane_areas_total: int | None = Field(None, alias="planeAreasTotal")
    num_vias: int | None = Field(None, alias="numVias")
    num_vias_to_planes: int | None = Field(None, alias="numViasToPlanes")
    num_vias_at_smd: int | None = Field(None, alias="numViasAtSmd")
    differential_pair_num_vias: int | None = Field(
        None, alias="differentialPairNumVias"
    )
    num_change_layer_vias: int | None = Field(None, alias="numChangeLayerVias")
    wire_length: float | None = Field(None, alias="wireLength")
    differential_pair_wire_length: float | None = Field(
        None, alias="differentialPairWireLength"
    )


class BoardRevisionDto(_Base):
    id: str | None = None
    revision_number: int | None = Field(None, alias="revisionNumber")
    result: RevisionResultDto | None = None
    json_file_path: str | None = Field(None, alias="jsonFilePath")
    ratsnets_file_path: str | None = Field(None, alias="ratsnetsFilePath")
    is_modified: bool | None = Field(None, alias="isModified")
    is_convergence_revision: bool | None = Field(None, alias="isConvergenceRevision")
    converged_after: int | None = Field(None, alias="convergedAfter")
    is_draft: bool | None = Field(None, alias="isDraft")


class BoardWorkflowDto(_Base):
    id: str | None = None
    workflow_id: str | None = Field(None, alias="workflowId")
    user_id: str | None = Field(None, alias="userId")
    user_role: str | int | None = Field(None, alias="userRole")
    status: str | int | None = None
    workflow_link: str | None = Field(None, alias="workflowLink")
    deep_pcb_version: str | None = Field(None, alias="deepPcbVersion")
    workflow_time_out: int | None = Field(None, alias="workflowTimeOut")
    workflow_max_batch_time_out: int | None = Field(
        None, alias="workflowMaxBatchTimeOut"
    )
    workflow_max_inactivity_wait_timeout: int | None = Field(
        None, alias="workflowMaxInactivityWaitTimeout"
    )
    workflow_time_to_live: int | None = Field(None, alias="workflowTimeToLive")
    workflow_signature: str | None = Field(None, alias="workflowSignature")
    workflow_cost_per_minute: float | None = Field(None, alias="workflowCostPerMinute")
    response_board_format: str | int | None = Field(None, alias="responseBoardFormat")
    started_on: datetime | None = Field(None, alias="startedOn")
    completed_on: datetime | None = Field(None, alias="completedOn")
    created_on: datetime | None = Field(None, alias="createdOn")
    last_revision_processing_time: str | None = Field(
        None, alias="lastRevisionProcessingTime"
    )
    workflow_type: str | int | None = Field(None, alias="workflowType")
    revisions: list[BoardRevisionDto] | None = None
    anomalies: list[BoardAnomalyDto] | None = None
    routing_anomalies: list[BoardAnomalyDto] | None = Field(
        None, alias="routingAnomalies"
    )
    placement_anomalies: list[BoardAnomalyDto] | None = Field(
        None, alias="placementAnomalies"
    )
    job_type: JobType | None = Field(None, alias="jobType")
    routing_type: RoutingType | None = Field(None, alias="routingType")
    reason: str | None = None
    routing_areas: RoutingAreasDto | None = Field(None, alias="routingAreas")


class BoardResultDto(_Base):
    air_wires_connected: int | None = Field(None, alias="airWiresConnected")
    nets_connected: int | None = Field(None, alias="netsConnected")
    via_added: int | None = Field(None, alias="viaAdded")
    total_wire_length: float | None = Field(None, alias="totalWireLength")
    air_wires_not_connected: int | None = Field(None, alias="airWiresNotConnected")
    total_air_wires: int | None = Field(None, alias="totalAirWires")
    total_components: int | None = Field(None, alias="totalComponents")
    placed_components: int | None = Field(None, alias="placedComponents")
    ratline_intersection: float | None = Field(None, alias="ratlineIntersection")
    constraint_ratline_distance: float | None = Field(
        None, alias="constraintRatlineDistance"
    )
    fitness_score: float | None = Field(None, alias="fitnessScore")
    ratline_distance: float | None = Field(None, alias="ratlineDistance")
    num_nets: int | None = Field(None, alias="numNets")
    num_nets_completed: int | None = Field(None, alias="numNetsCompleted")
    differential_pair_num_connections: int | None = Field(
        None, alias="differentialPairNumConnections"
    )
    differential_pair_num_connections_completed: int | None = Field(
        None, alias="differentialPairNumConnectionsCompleted"
    )
    differential_pair_num_connections_missing: int | None = Field(
        None, alias="differentialPairNumConnectionsMissing"
    )
    differential_pair_num_nets: int | None = Field(
        None, alias="differentialPairNumNets"
    )
    differential_pair_num_nets_completed: int | None = Field(
        None, alias="differentialPairNumNetsCompleted"
    )
    num_plane_parts: int | None = Field(None, alias="numPlaneParts")
    num_planes: int | None = Field(None, alias="numPlanes")
    num_plane_connections: int | None = Field(None, alias="numPlaneConnections")
    num_plane_cuts: int | None = Field(None, alias="numPlaneCuts")
    plane_areas_total: int | None = Field(None, alias="planeAreasTotal")
    num_vias_to_planes: int | None = Field(None, alias="numViasToPlanes")
    num_vias_at_smd: int | None = Field(None, alias="numViasAtSmd")
    differential_pair_num_vias: int | None = Field(
        None, alias="differentialPairNumVias"
    )
    num_change_layer_vias: int | None = Field(None, alias="numChangeLayerVias")
    differential_pair_wire_length: float | None = Field(
        None, alias="differentialPairWireLength"
    )
    created_on: datetime | None = Field(None, alias="createdOn")
    total_processing_time: str | None = Field(None, alias="totalProcessingTime")
    finished_on: datetime | None = Field(None, alias="finishedOn")


# ---------------------------------------------------------------------------
# Top-level response models
# ---------------------------------------------------------------------------


class ApiBoardDto(_Base):
    """Board summary returned by GET /details."""

    board_id: str = Field(alias="boardId")
    board_p_id: str | None = Field(None, alias="boardPId")
    name: str | None = None
    input_type: BoardInputType | None = Field(None, alias="inputType")
    created_on: datetime = Field(alias="createdOn")
    board_status: BoardStatus = Field(alias="boardStatus")
    request_id: str | RequestIdDto | None = Field(None, alias="requestId")
    board_view_url: str | None = Field(None, alias="boardViewUrl")


class BoardWithRevisionsDto(_Base):
    """Full board with revisions returned by GET /boards/{boardId}."""

    user_id: str | None = Field(None, alias="userId")
    board_id: str = Field(alias="boardId")
    board_p_id: str | None = Field(None, alias="boardPId")
    name: str | None = None
    input_type: BoardInputType | None = Field(None, alias="inputType")
    type: str | int | None = None
    dsn_file: str | None = Field(None, alias="dsnFile")
    json_file: str | None = Field(None, alias="jsonFile")
    sanitized_json_file: str | None = Field(None, alias="sanitizedJsonFile")
    created_on: datetime = Field(alias="createdOn")
    layers: int | None = None
    pins: int | None = None
    rats_nest: int | None = Field(None, alias="ratsNest")
    total_air_wires: int = Field(alias="totalAirWires")
    total_components: int | None = Field(None, alias="totalComponents")
    is_free: bool = Field(alias="isFree")
    confirmed: bool | None = None
    created_by: str | None = Field(None, alias="createdBy")
    price_status: PricingStatus | None = Field(None, alias="priceStatus")
    board_status: BoardStatus | None = Field(None, alias="boardStatus")
    response_format: str | int | None = Field(None, alias="responseFormat")
    workflow_started_on: datetime | None = Field(None, alias="workflowStartedOn")
    board_resumed_on: datetime | None = Field(None, alias="boardResumedOn")
    rl_version: str | None = Field(None, alias="rlVersion")
    rl_mode: str | None = Field(None, alias="rlMode")
    requires_credits: bool | None = Field(None, alias="requiresCredits")
    result: BoardResultDto | None = None
    ratings_and_feedback: BoardRatingsAndFeedbackDto | None = Field(
        None, alias="ratingsAndFeedback"
    )
    warnings: list[str] | None = None
    credits_cost_per_minute: float | None = Field(None, alias="creditsCostPerMinute")
    workflow_credits: float | None = Field(None, alias="workflowCredits")
    workflow_burned_credits: float | None = Field(None, alias="workflowBurnedCredits")
    board_source_type: str | int | None = Field(None, alias="boardSourceType")
    web_hook_url: str | None = Field(None, alias="webHookUrl")
    web_hook_token: str | None = Field(None, alias="webHookToken")
    request_id: str | RequestIdDto | None = Field(None, alias="requestId")
    batch: int | None = None
    resolution: ResolutionDto | None = None
    starter_kit_initial_timeout: str | None = Field(
        None, alias="starterKitInitialTimeout"
    )
    starter_kit_timeout_left: str | None = Field(None, alias="starterKitTimeoutLeft")
    starter_kit_max_runs_count: int | None = Field(None, alias="starterKitMaxRunsCount")
    is_patched: bool | None = Field(None, alias="isPatched")
    is_created_by_admin: bool | None = Field(None, alias="isCreatedByAdmin")
    is_valid_for_routing: bool | None = Field(None, alias="isValidForRouting")
    is_valid_for_placement: bool | None = Field(None, alias="isValidForPlacement")
    workflows: list[BoardWorkflowDto] | None = None
    workflow: BoardWorkflowDto | None = None


class BoardCheckedDto(_Base):
    """Response from POST /boards/check-board."""

    is_valid: bool | None = Field(None, alias="isValid")
    board_json: str | None = Field(None, alias="boardJson")
    warnings: list[str] | None = None
    anomalies: list[BoardAnomalyDto] | None = None
    error: str | None = None


class ConstraintsResponseDto(_Base):
    """Response from POST /boards/check-constraints."""

    is_valid: bool | None = Field(None, alias="isValid")
    error: str | None = None


class BatchRevision(_Base):
    file_url: str | None = Field(None, alias="fileUrl")
    stats: dict[str, Any] | None = None
    generated_on: datetime | None = Field(None, alias="generatedOn")
    credits_burned: float | None = Field(None, alias="creditsBurned")
    committed: bool | None = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateBoardRequest(_Base):
    """POST /api/v1/boards — multipart/form-data fields."""

    routing_type: RoutingType | None = Field(default=None, alias="routingType")
    board_name: str | None = Field(default=None, alias="boardName")
    request_id: str | None = Field(default=None, alias="requestId")
    webhook_url: str | None = Field(default=None, alias="webhookUrl")
    webhook_token: str | None = Field(default=None, alias="webhookToken")
    board_input_type: BoardInputType | None = Field(
        default=None, alias="boardInputType"
    )
    json_file_url: str | None = Field(default=None, alias="jsonFileUrl")


class ConfirmBoardRequest(_Base):
    """PATCH /api/v1/boards/{boardId}/confirm."""

    job_type: JobType | None = Field(default=None, alias="jobType")
    routing_type: RoutingType | None = Field(default=None, alias="routingType")
    timeout: int | None = None
    max_batch_timeout: int | None = Field(default=None, alias="maxBatchTimeout")
    time_to_live: int | None = Field(default=None, alias="timeToLive")
    max_inactivity_wait_timeout: int | None = Field(
        default=None, alias="maxInactivityWaitTimeout"
    )
    constraints_file_url: str | None = Field(default=None, alias="constraintsFileUrl")
    response_board_format: str | int | None = Field(
        default=None, alias="responseBoardFormat"
    )


class ResumeBoardRequest(_Base):
    """PATCH /api/v1/boards/{boardId}/resume."""

    job_type: JobType | None = Field(default=None, alias="jobType")
    routing_type: RoutingType | None = Field(default=None, alias="routingType")
    timeout: int | None = None
    max_batch_timeout: int | None = Field(default=None, alias="maxBatchTimeout")
    response_board_format: str | int | None = Field(
        default=None, alias="responseBoardFormat"
    )
    time_to_live: int | None = Field(default=None, alias="timeToLive")
    max_inactivity_wait_timeout: int | None = Field(
        default=None, alias="maxInactivityWaitTimeout"
    )
    constraints_file_url: str | None = Field(default=None, alias="constraintsFileUrl")


# ---------------------------------------------------------------------------
# Credit flow models
# ---------------------------------------------------------------------------


class ApiBalanceChangeDto(_Base):
    # The OpenAPI spec doesn't detail all fields; keep it flexible.
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ApiUserCreditFlowDto(_Base):
    balance: float
    balance_changes: list[ApiBalanceChangeDto] | None = Field(
        None, alias="balanceChanges"
    )
    used_credits: float | None = Field(None, alias="usedCredits")
    created_boards: int | None = Field(None, alias="createdBoards")


# ---------------------------------------------------------------------------
# Board file format models (for .deeppcb files)
# ---------------------------------------------------------------------------


class Shape(_Base):
    """Geometric shape used throughout the board format."""

    type: str
    center: list[float] | None = None
    radius: float | None = None
    lower_left: list[float] | None = Field(None, alias="lowerLeft")
    upper_right: list[float] | None = Field(None, alias="upperRight")
    points: list[list[float]] | None = None
    width: float | None = None
    outline: list[list[float]] | None = None
    holes: list[list[list[float]]] | None = None
    shapes: list[Shape] | None = None


class Boundary(_Base):
    shape: Shape
    clearance: int | None = None
    user_data: str | None = Field(None, alias="userData")


class PadBasic(_Base):
    shape: Shape
    layer_from: int | None = Field(None, alias="layerFrom")
    layer_to: int | None = Field(None, alias="layerTo")


class Hole(_Base):
    shape: Shape


class Padstack(_Base):
    """Padstack — covers both basic and advanced variants."""

    id: str
    layers: list[int] | None = None
    shape: Shape | None = None
    pads: list[PadBasic] | None = None
    hole: Hole | None = None
    allow_via: bool | None = Field(None, alias="allowVia")


class Keepout(_Base):
    shape: Shape
    layer: int
    type: list[str] | None = None
    user_data: str | None = Field(None, alias="userData")


class Pin(_Base):
    id: str
    padstack: str
    position: list[int]
    rotation: int


class ComponentDefinition(_Base):
    id: str
    keepouts: list[Keepout]
    pins: list[Pin]
    outline: Shape | None = None


class Component(_Base):
    id: str
    definition: str
    part_number: str | None = Field(None, alias="partNumber")
    position: list[int]
    rotation: int
    side: str
    protected: bool | None = None
    user_data: str | None = Field(None, alias="userData")


class Layer(_Base):
    id: str
    display_name: str | None = Field(None, alias="displayName")
    keepouts: list[Keepout]
    type: str | None = None


class Net(_Base):
    id: str
    pins: list[str]
    track_width: int | list[int] | None = Field(None, alias="trackWidth")
    routing_priority: int | None = Field(None, alias="routingPriority")
    forbidden_layers: list[int] | None = Field(None, alias="forbiddenLayers")


class NetClass(_Base):
    id: str
    nets: list[str]
    clearance: int
    track_width: int | list[int] = Field(alias="trackWidth")
    via_definition: str | None = Field(None, alias="viaDefinition")
    via_priority: list[list[str]] | None = Field(None, alias="viaPriority")


class NetPreference(_Base):
    id: str
    nets: list[str]
    reduce_via_count_prio_coef: int | None = Field(None, alias="reduceViaCountPrioCoef")
    reduce_wire_length_prio_coef: int | None = Field(
        None, alias="reduceWireLengthPrioCoef"
    )
    reduce_acute_angle_prio_coef: int | None = Field(
        None, alias="reduceAcuteAnglePrioCoef"
    )


class DifferentialPair(_Base):
    net_id1: str = Field(alias="netId1")
    net_id2: str = Field(alias="netId2")
    track_width: int | list[int] | None = Field(None, alias="trackWidth")
    gap: int | None = None


class RuleSubject(_Base):
    id: str | None = None
    type: str | None = None


class Rule(_Base):
    value: Any
    type: str
    subjects: list[RuleSubject] | None = None
    description: str | None = None


class Plane(_Base):
    net_id: str = Field(alias="netId")
    layer: int
    shape: Shape
    protected: bool | None = None
    keepout_rule: list[str] | str | None = Field(None, alias="keepoutRule")
    user_data: str | None = Field(None, alias="userData")


class Wire(_Base):
    net_id: str = Field(alias="netId")
    layer: int
    start: list[int]
    end: list[int]
    width: int
    type: str
    protected: bool | None = None
    user_data: str | None = Field(None, alias="userData")


class Via(_Base):
    net_id: str = Field(alias="netId")
    position: list[int]
    padstack: str
    protected: bool | None = None
    user_data: str | None = Field(None, alias="userData")


class BoardResolution(_Base):
    unit: str
    value: int


class DeepPCBBoard(_Base):
    """Top-level model for a .deeppcb board file."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    boundary: Boundary
    padstacks: list[Padstack]
    component_definitions: list[ComponentDefinition] = Field(
        alias="componentDefinitions"
    )
    components: list[Component]
    layers: list[Layer]
    nets: list[Net]
    net_classes: list[NetClass] = Field(alias="netClasses")
    net_preferences: list[NetPreference] | None = Field(None, alias="netPreferences")
    differential_pairs: list[DifferentialPair] | None = Field(
        None, alias="differentialPairs"
    )
    rules: list[Rule] | None = None
    resolution: BoardResolution
    planes: list[Plane]
    wires: list[Wire]
    vias: list[Via]
    via_definitions: list[str] = Field(alias="viaDefinitions")


# ---------------------------------------------------------------------------
# Constraints file models
# ---------------------------------------------------------------------------


class ConstraintSchema(_Base):
    type: str
    targets: list[str]


class DecouplingConstraints(_Base):
    """Dynamic keys mapping pin IDs to constraint lists."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class NetTypeConstraint(_Base):
    type: str
    targets: list[str]


class BoardConstraints(_Base):
    """Top-level model for a constraints file."""

    decoupling_constraints: DecouplingConstraints | None = None
    net_type_constraints: list[NetTypeConstraint] | None = None
