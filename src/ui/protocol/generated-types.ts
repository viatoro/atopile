// Generated from src/atopile/data_models.py by atopile.generate_types
// Do not edit by hand.

type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

function cloneGenerated<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export type AutolayoutState = "building" | "submitting" | "queued" | "running" | "awaiting_selection" | "completed" | "failed" | "cancelled";

export type BuildStatus = "queued" | "building" | "success" | "warning" | "failed" | "cancelled";

export type PinSignalType = "logic" | "signal" | "power" | "nc";

export type StageStatus = "pending" | "running" | "success" | "warning" | "failed" | "skipped";

export type StdLibItemType = "interface" | "module" | "component" | "trait" | "parameter";

export type UiAudience = "user" | "developer" | "agent";

export type UiLogLevel = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "ALERT";

export interface AddBuildTargetRequest {
  projectRoot: string;
  name: string;
  entry: string;
}

export interface AddBuildTargetResponse {
  success: boolean;
  message: string;
  target: string | null;
}

export interface Build {
  name: string;
  projectName: string | null;
  buildId: string | null;
  status: BuildStatus;
  elapsedSeconds: number;
  warnings: number;
  errors: number;
  returnCode: number | null;
  error: string | null;
  projectRoot: string | null;
  target: ResolvedBuildTarget;
  startedAt: number | null;
  standalone: boolean;
  frozen: boolean | null;
  stages: BuildStage[];
  totalStages: number | null;
}

export interface BuildRequest {
  projectRoot: string;
  targets: ResolvedBuildTarget[];
  frozen: boolean;
  entry: string | null;
  standalone: boolean;
  includeTargets: string[];
  excludeTargets: string[];
}

export interface BuildStage {
  name: string;
  stageId: string;
  elapsedSeconds: number;
  status: StageStatus;
  infos: number;
  warnings: number;
  errors: number;
}

export interface BuildsResponse {
  builds: Build[];
  total: number | null;
}

export interface CreateProjectRequest {
  parentDirectory: string;
  name: string | null;
}

export interface CreateProjectResponse {
  success: boolean;
  message: string;
  projectRoot: string | null;
  projectName: string | null;
}

export interface DeleteBuildTargetRequest {
  projectRoot: string;
  name: string;
}

export interface DeleteBuildTargetResponse {
  success: boolean;
  message: string;
}

export interface DependenciesResponse {
  dependencies: DependencyInfo[];
  total: number;
}

export interface DependencyInfo {
  identifier: string;
  version: string;
  latestVersion: string | null;
  name: string;
  publisher: string;
  repository: string | null;
  hasUpdate: boolean;
  isDirect: boolean;
  via: string[] | null;
  status: string | null;
}

export interface FileNode {
  name: string;
  children: FileNode[] | null;
}

export interface ManufacturingArtifact {
  name: string;
  path: string;
  sizeBytes: number;
}

export interface ModuleChild {
  name: string;
  typeName: string;
  itemType: "interface" | "module" | "component" | "parameter" | "trait";
  children: ModuleChild[];
  spec: string | null;
  srcLoc: string | null;
}

export interface ModuleDefinition {
  name: string;
  type: "module" | "interface" | "component";
  file: string;
  entry: string;
  line: number | null;
  superType: string | null;
  children: ModuleChild[];
}

export interface ModulesResponse {
  modules: ModuleDefinition[];
  total: number;
}

export interface OpenLayoutRequest {
  projectRoot: string;
  target: ResolvedBuildTarget;
}

export interface PackageActionRequest {
  packageIdentifier: string;
  projectRoot: string;
  version: string | null;
}

export interface PackageActionResponse {
  success: boolean;
  message: string;
  action: string;
}

export interface PackageArtifact {
  filename: string;
  url: string;
  size: number;
  hashes: PackageFileHashes;
  buildName: string | null;
}

export interface PackageAuthor {
  name: string;
  email: string | null;
}

export interface PackageDependency {
  identifier: string;
  version: string | null;
}

export interface PackageDetails {
  identifier: string;
  name: string;
  publisher: string;
  version: string;
  createdAt: string | null;
  releasedAt: string | null;
  authors: PackageAuthor[];
  summary: string | null;
  description: string | null;
  homepage: string | null;
  repository: string | null;
  license: string | null;
  downloads: number | null;
  downloadsThisWeek: number | null;
  downloadsThisMonth: number | null;
  versions: PackageVersion[];
  readme: string | null;
  builds: string[] | null;
  artifacts: PackageArtifact[];
  layouts: PackageLayout[];
  importStatements: PackageImportStatement[];
  installed: boolean;
  installedVersion: string | null;
  dependencies: PackageDependency[];
}

export interface PackageFileHashes {
  sha256: string;
}

export interface PackageImportStatement {
  buildName: string;
  importStatement: string;
}

export interface PackageInfo {
  identifier: string;
  name: string;
  publisher: string;
  version: string | null;
  latestVersion: string | null;
  description: string | null;
  summary: string | null;
  homepage: string | null;
  repository: string | null;
  license: string | null;
  installed: boolean;
  hasUpdate: boolean;
  downloads: number | null;
  keywords: string[] | null;
}

export interface PackageInfoVeryBrief {
  identifier: string;
  version: string;
  summary: string;
}

export interface PackageLayout {
  buildName: string;
  url: string;
}

export interface PackageSummaryItem {
  identifier: string;
  name: string;
  publisher: string;
  installed: boolean;
  version: string | null;
  latestVersion: string | null;
  hasUpdate: boolean;
  summary: string | null;
  description: string | null;
  homepage: string | null;
  repository: string | null;
  license: string | null;
  downloads: number | null;
  keywords: string[];
}

export interface PackageVersion {
  version: string;
  releasedAt: string | null;
  requiresAtopile: string | null;
  size: number | null;
}

export interface PackagesResponse {
  packages: PackageInfo[];
  total: number;
}

export interface PackagesSummaryData {
  packages: PackageSummaryItem[];
  total: number;
  installedCount: number;
}

export interface PinoutComponent {
  name: string;
  atoAddress: string;
  designator: string;
  descriptor: string;
  typeName: string;
  footprintUuid: string | null;
  leads: PinoutLead[];
  warnings: string[];
}

export interface PinoutLead {
  leadDesignator: string;
  padNumbers: string[];
  netName: string | null;
  signalType: PinSignalType;
  interfaces: string[];
  isConnected: boolean;
}

export interface Project {
  root: string;
  name: string;
  targets: ResolvedBuildTarget[];
  needsMigration: boolean;
  error: string | null;
  summary: string | null;
  identifier: string | null;
}

export interface ProjectsResponse {
  projects: Project[];
  total: number;
}

export interface RegistrySearchResponse {
  packages: PackageInfo[];
  total: number;
  query: string;
}

export interface RenameProjectRequest {
  projectRoot: string;
  newName: string;
}

export interface RenameProjectResponse {
  success: boolean;
  message: string;
  oldRoot: string;
  newRoot: string | null;
}

export interface ResolvedBuildTarget {
  name: string;
  entry: string;
  pcbPath: string;
  modelPath: string;
  root: string;
}

export interface StdLibChild {
  name: string;
  type: string;
  itemType: StdLibItemType;
  children: StdLibChild[];
  enumValues: string[];
}

export interface StdLibData {
  items: StdLibItem[];
  total: number;
}

export interface StdLibItem {
  id: string;
  name: string;
  type: StdLibItemType;
  description: string;
  usage: string | null;
  children: StdLibChild[];
  parameters: Record<string, string>[];
}

export interface SyncPackagesRequest {
  projectRoot: string;
  force: boolean;
}

export interface SyncPackagesResponse {
  success: boolean;
  message: string;
  operationId: string | null;
  modifiedPackages: string[] | null;
}

export interface UiActionMessage {
  type: "action";
  action: string;
  [key: string]: unknown;
}

export interface UiActionResultMessage {
  type: "action_result";
  requestId: string | null;
  action: string;
  ok: boolean | null;
  result: unknown;
  error: string | null;
  [key: string]: unknown;
}

export interface UiAgentChecklistData {
  items: UiAgentChecklistItemData[];
}

export interface UiAgentChecklistItemData {
  id: string;
  description: string;
  status: string;
}

export interface UiAgentData {
  loaded: boolean;
  sessions: UiAgentSessionData[];
  defaultModel: string;
  lastMutation: UiAgentMutation | null;
}

export interface UiAgentDesignQuestionData {
  id: string;
  question: string;
  options: string[];
  default: string | null;
}

export interface UiAgentDesignQuestionsData {
  context: string;
  questions: UiAgentDesignQuestionData[];
}

export interface UiAgentMessageData {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  pending: boolean;
  reasoning: string | null;
  toolTraces: UiAgentToolTraceData[];
  designQuestions: UiAgentDesignQuestionsData | null;
  errorContext: Record<string, unknown> | null;
}

export interface UiAgentMutation {
  action: string | null;
  sessionId: string | null;
  runId: string | null;
  error: string | null;
  updatedAt: number | null;
}

export interface UiAgentSessionData {
  sessionId: string;
  projectRoot: string;
  model: string;
  messages: UiAgentMessageData[];
  checklist: UiAgentChecklistData | null;
  activeRunId: string | null;
  activeRunStatus: string | null;
  activeRunStopRequested: boolean;
  error: string | null;
  createdAt: number;
  updatedAt: number;
}

export interface UiAgentToolTraceData {
  name: string;
  label: string;
  args: Record<string, unknown>;
  ok: boolean;
  result: Record<string, unknown>;
  callId: string | null;
  running: boolean;
}

export interface UiAutolayoutCandidateData {
  candidateId: string;
  label: string | null;
  score: number | null;
  routedPct: number | null;
  viaCount: number | null;
  metadata: Record<string, unknown>;
  files: Record<string, string>;
}

export interface UiAutolayoutData {
  loading: boolean;
  error: string | null;
  submitting: boolean;
  jobType: string;
  timeoutMinutes: number;
  jobs: UiAutolayoutJobData[];
  preflight: UiAutolayoutPreflightData | null;
  preflightLoading: boolean;
  preflightError: string | null;
  placementReadiness: UiAutolayoutPreCheckItem[];
  routingReadiness: UiAutolayoutPreCheckItem[];
  previewJobId: string | null;
  previewCandidateId: string | null;
  previewPath: string | null;
  diffPathA: string | null;
  diffPathB: string | null;
}

export interface UiAutolayoutJobData {
  jobId: string;
  projectRoot: string;
  buildTarget: string;
  provider: string;
  jobType: string;
  state: AutolayoutState;
  displayState: string;
  createdAt: string;
  updatedAt: string;
  buildId: string | null;
  providerJobRef: string | null;
  progress: number | null;
  message: string;
  error: string | null;
  selectedCandidateId: string | null;
  appliedCandidateId: string | null;
  recommendedCandidateId: string | null;
  layoutPath: string | null;
  candidates: UiAutolayoutCandidateData[];
}

export interface UiAutolayoutPreCheckItem {
  label: string;
  passed: boolean;
  detail: string;
}

export interface UiAutolayoutPreflightData {
  boardAreaMm2: number | null;
  boardWidthMm: number | null;
  boardHeightMm: number | null;
  componentCount: number;
  topComponentCount: number;
  bottomComponentCount: number;
  componentsInsideBoard: number;
  componentsOutsideBoard: number;
  componentAreaMm2: number | null;
  padCount: number;
  netCount: number;
  connectionCount: number;
  placementUtilization: number | null;
  topOnlyUtilization: number | null;
  padDensity: number | null;
  connectionDensity: number | null;
  layerCount: number | null;
  sidedness: string;
  stackupRisk: string;
  recommendation: string;
}

export interface UiBOMComponent {
  id: string;
  lcsc: string | null;
  mpn: string;
  manufacturer: string;
  type: string | null;
  value: string;
  package: string;
  description: string | null;
  source: string | null;
  datasheet: string | null;
  stock: number | null;
  unitCost: number | null;
  isBasic: boolean | null;
  isPreferred: boolean | null;
  quantity: number;
  parameters: UiBOMParameter[];
  usages: UiBOMUsage[];
}

export interface UiBOMData {
  projectRoot: string | null;
  target: ResolvedBuildTarget | null;
  loading: boolean;
  error: string | null;
  errorTraceback: string | null;
  version: string | null;
  buildId: string | null;
  components: UiBOMComponent[];
  totalQuantity: number;
  uniqueParts: number;
  estimatedCost: number | null;
  outOfStock: number;
}

export interface UiBOMParameter {
  name: string;
  value: string;
  unit: string | null;
}

export interface UiBOMUsage {
  address: string;
  designator: string;
  line: number | null;
}

export interface UiBlobAssetData {
  action: string | null;
  requestKey: string;
  contentType: string | null;
  filename: string | null;
  data: string | null;
  loading: boolean;
  error: string | null;
}

export interface UiBuildLogRequest {
  buildId: string;
  stage: string | null;
  logLevels: UiLogLevel[] | null;
  audience: UiAudience | null;
  count: number | null;
}

export interface UiBuildsByProjectData {
  projectRoot: string | null;
  target: ResolvedBuildTarget | null;
  limit: number;
  builds: Build[];
  loading: boolean;
}

export interface UiCoreStatus {
  uvPath: string;
  atoBinary: string;
  mode: "local" | "production";
  version: string;
  coreServerPort: number;
}

export interface UiEntryCheckData {
  projectRoot: string | null;
  entry: string;
  fileExists: boolean;
  moduleExists: boolean;
  targetExists: boolean;
  loading: boolean;
}

export interface UiExtensionSettings {
  enableChat: boolean;
}

export interface UiFileActionData {
  action: "none" | "create_file" | "create_folder" | "rename" | "duplicate" | "delete";
  path: string | null;
  isFolder: boolean;
}

export interface UiLayoutData {
  projectRoot: string | null;
  target: ResolvedBuildTarget | null;
  path: string | null;
  revision: number;
  loading: boolean;
  error: string | null;
  readOnly: boolean;
}

export interface UiLcscPartData {
  manufacturer: string | null;
  mpn: string | null;
  description: string | null;
  stock: number | null;
  unitCost: number | null;
  isBasic: boolean | null;
  isPreferred: boolean | null;
}

export interface UiLcscPartsData {
  projectRoot: string | null;
  target: ResolvedBuildTarget | null;
  parts: Record<string, UiLcscPartData | null>;
  loadingIds: string[];
}

export interface UiLogEntry {
  id: number | null;
  timestamp: string;
  level: UiLogLevel;
  audience: UiAudience;
  loggerName: string;
  message: string;
  testName: string | null;
  stage: string | null;
  sourceFile: string | null;
  sourceLine: number | null;
  atoTraceback: string | null;
  pythonTraceback: string | null;
  objects: unknown | null;
}

export interface UiLogsErrorMessage {
  type: "logs_error";
  error: string;
}

export interface UiLogsStreamMessage {
  type: "logs_stream";
  buildId: string;
  stage: string | null;
  logs: UiLogEntry[];
  lastId: number;
}

export interface UiMigrationState {
  projectRoot: string | null;
  projectName: string | null;
  needsMigration: boolean;
  steps: UiMigrationStep[];
  topics: UiMigrationTopic[];
  stepResults: UiMigrationStepResult[];
  loading: boolean;
  running: boolean;
  completed: boolean;
  error: string | null;
}

export interface UiMigrationStep {
  id: string;
  label: string;
  description: string;
  topic: string;
  mandatory: boolean;
  order: number;
}

export interface UiMigrationStepResult {
  stepId: string;
  status: "idle" | "running" | "success" | "error";
  error: string | null;
  syncProgress: UiPackageSyncProgress | null;
}

export interface UiMigrationTopic {
  id: string;
  label: string;
  icon: string;
}

export interface UiPackageDetailState {
  projectRoot: string | null;
  packageId: string | null;
  summary: PackageSummaryItem | null;
  details: PackageDetails | null;
  loading: boolean;
  error: string | null;
  actionError: string | null;
  syncProgress: UiPackageSyncProgress | null;
}

export interface UiPackageSyncProgress {
  stage: string;
  message: string;
  completed: number;
  total: number;
}

export interface UiPartData {
  identifier: string;
  lcsc: string | null;
  mpn: string;
  manufacturer: string;
  description: string;
  package: string | null;
  datasheetUrl: string | null;
  path: string | null;
  stock: number | null;
  unitCost: number | null;
  isBasic: boolean;
  isPreferred: boolean;
  attributes: Record<string, string>;
  footprint: string | null;
  imageUrl: string | null;
  importStatement: string | null;
  installed: boolean;
  action: "idle" | "installing" | "uninstalling" | "converting";
}

export interface UiPartDetailState {
  projectRoot: string | null;
  lcsc: string | null;
  details: UiPartData | null;
  loading: boolean;
  error: string | null;
  actionError: string | null;
}

export interface UiPartsSearchData {
  projectRoot: string | null;
  query: string;
  installedOnly: boolean;
  parts: UiPartData[];
  loading: boolean;
  error: string | null;
  actionError: string | null;
}

export interface UiPinoutData {
  projectRoot: string | null;
  target: ResolvedBuildTarget | null;
  error: string | null;
  components: PinoutComponent[];
}

export interface UiProjectFilesData {
  projectRoot: string | null;
  files: FileNode[];
  loading: boolean;
}

export interface UiProjectState {
  selectedProjectRoot: string | null;
  selectedTarget: ResolvedBuildTarget | null;
  activeFilePath: string | null;
  logViewBuildId: string | null;
  logViewStage: string | null;
}

export interface UiRecentBuildsData {
  builds: Build[];
}

export interface UiSidebarDetails {
  view: "none" | "package" | "part" | "migration";
  package: UiPackageDetailState;
  part: UiPartDetailState;
  migration: UiMigrationState;
}

export interface UiStackupData {
  projectRoot: string | null;
  target: ResolvedBuildTarget | null;
  loading: boolean;
  error: string | null;
  errorTraceback: string | null;
  version: string | null;
  stackupName: string | null;
  manufacturer: UiStackupManufacturer | null;
  layers: UiStackupLayer[];
  layerCount: number;
  totalThicknessMm: number | null;
}

export interface UiStackupLayer {
  index: number;
  layerType: string | null;
  material: string | null;
  thicknessMm: number | null;
  relativePermittivity: number | null;
  lossTangent: number | null;
}

export interface UiStackupManufacturer {
  name: string;
  country: string | null;
  website: string | null;
}

export interface UiStateMessage {
  type: "state";
  key: StoreKey;
  data: unknown;
}

export interface UiStore {
  coreStatus: UiCoreStatus;
  extensionSettings: UiExtensionSettings;
  projectState: UiProjectState;
  projects: Project[];
  projectFiles: UiProjectFilesData;
  currentBuilds: Build[];
  previousBuilds: Build[];
  queueBuilds: Build[];
  selectedBuild: Build | null;
  selectedBuildInProgress: boolean;
  packagesSummary: PackagesSummaryData;
  partsSearch: UiPartsSearchData;
  sidebarDetails: UiSidebarDetails;
  stdlibData: StdLibData;
  structureData: UiStructureData;
  variablesData: UiVariablesData;
  bomData: UiBOMData;
  stackupData: UiStackupData;
  pinoutData: UiPinoutData;
  entryCheck: UiEntryCheckData;
  lcscPartsData: UiLcscPartsData;
  buildsByProjectData: UiBuildsByProjectData;
  recentBuildsData: UiRecentBuildsData;
  layoutData: UiLayoutData;
  blobAsset: UiBlobAssetData;
  fileAction: UiFileActionData;
  agentData: UiAgentData;
  autolayoutData: UiAutolayoutData;
  authState: Record<string, unknown>;
}

export interface UiStructureData {
  projectRoot: string | null;
  modules: ModuleDefinition[];
  total: number;
  loading: boolean;
  error: string | null;
}

export interface UiSubscribeMessage {
  type: "subscribe";
  keys: StoreKey[];
}

export interface UiTestLogRequest {
  testRunId: string;
  testName: string | null;
  logLevels: UiLogLevel[] | null;
  audience: UiAudience | null;
  count: number | null;
}

export interface UiVariable {
  name: string;
  spec: string | null;
  actual: string | null;
  meetsSpec: boolean | null;
}

export interface UiVariableNode {
  name: string;
  variables: UiVariable[];
  children: UiVariableNode[];
}

export interface UiVariablesData {
  nodes: UiVariableNode[];
}

export interface UpdateBuildTargetRequest {
  projectRoot: string;
  oldName: string;
  newName: string | null;
  newEntry: string | null;
}

export interface UpdateBuildTargetResponse {
  success: boolean;
  message: string;
  target: string | null;
}

export const STORE_KEYS = ["agentData", "authState", "autolayoutData", "blobAsset", "bomData", "buildsByProjectData", "coreStatus", "currentBuilds", "entryCheck", "extensionSettings", "fileAction", "layoutData", "lcscPartsData", "packagesSummary", "partsSearch", "pinoutData", "previousBuilds", "projectFiles", "projectState", "projects", "queueBuilds", "recentBuildsData", "selectedBuild", "selectedBuildInProgress", "sidebarDetails", "stackupData", "stdlibData", "structureData", "variablesData"] as const;
export type StoreKey = typeof STORE_KEYS[number];

export const DEFAULT_PinoutComponent: PinoutComponent = {
  "atoAddress": "",
  "descriptor": "",
  "designator": "",
  "footprintUuid": null,
  "leads": [],
  "name": "",
  "typeName": "",
  "warnings": []
};

export function createPinoutComponent(): PinoutComponent {
  return cloneGenerated(DEFAULT_PinoutComponent);
}

export const DEFAULT_UiAgentChecklistData: UiAgentChecklistData = {
  "items": []
};

export function createUiAgentChecklistData(): UiAgentChecklistData {
  return cloneGenerated(DEFAULT_UiAgentChecklistData);
}

export const DEFAULT_UiAgentChecklistItemData: UiAgentChecklistItemData = {
  "description": "",
  "id": "",
  "status": "pending"
};

export function createUiAgentChecklistItemData(): UiAgentChecklistItemData {
  return cloneGenerated(DEFAULT_UiAgentChecklistItemData);
}

export const DEFAULT_UiAgentData: UiAgentData = {
  "defaultModel": "",
  "lastMutation": null,
  "loaded": false,
  "sessions": []
};

export function createUiAgentData(): UiAgentData {
  return cloneGenerated(DEFAULT_UiAgentData);
}

export const DEFAULT_UiAgentDesignQuestionData: UiAgentDesignQuestionData = {
  "default": null,
  "id": "",
  "options": [],
  "question": ""
};

export function createUiAgentDesignQuestionData(): UiAgentDesignQuestionData {
  return cloneGenerated(DEFAULT_UiAgentDesignQuestionData);
}

export const DEFAULT_UiAgentDesignQuestionsData: UiAgentDesignQuestionsData = {
  "context": "",
  "questions": []
};

export function createUiAgentDesignQuestionsData(): UiAgentDesignQuestionsData {
  return cloneGenerated(DEFAULT_UiAgentDesignQuestionsData);
}

export const DEFAULT_UiAgentMessageData: UiAgentMessageData = {
  "content": "",
  "designQuestions": null,
  "errorContext": null,
  "id": "",
  "pending": false,
  "reasoning": null,
  "role": "system",
  "toolTraces": []
};

export function createUiAgentMessageData(): UiAgentMessageData {
  return cloneGenerated(DEFAULT_UiAgentMessageData);
}

export const DEFAULT_UiAgentMutation: UiAgentMutation = {
  "action": null,
  "error": null,
  "runId": null,
  "sessionId": null,
  "updatedAt": null
};

export function createUiAgentMutation(): UiAgentMutation {
  return cloneGenerated(DEFAULT_UiAgentMutation);
}

export const DEFAULT_UiAgentSessionData: UiAgentSessionData = {
  "activeRunId": null,
  "activeRunStatus": null,
  "activeRunStopRequested": false,
  "checklist": null,
  "createdAt": 0.0,
  "error": null,
  "messages": [],
  "model": "",
  "projectRoot": "",
  "sessionId": "",
  "updatedAt": 0.0
};

export function createUiAgentSessionData(): UiAgentSessionData {
  return cloneGenerated(DEFAULT_UiAgentSessionData);
}

export const DEFAULT_UiAgentToolTraceData: UiAgentToolTraceData = {
  "args": {},
  "callId": null,
  "label": "",
  "name": "",
  "ok": true,
  "result": {},
  "running": false
};

export function createUiAgentToolTraceData(): UiAgentToolTraceData {
  return cloneGenerated(DEFAULT_UiAgentToolTraceData);
}

export const DEFAULT_UiAutolayoutCandidateData: UiAutolayoutCandidateData = {
  "candidateId": "",
  "files": {},
  "label": null,
  "metadata": {},
  "routedPct": null,
  "score": null,
  "viaCount": null
};

export function createUiAutolayoutCandidateData(): UiAutolayoutCandidateData {
  return cloneGenerated(DEFAULT_UiAutolayoutCandidateData);
}

export const DEFAULT_UiAutolayoutData: UiAutolayoutData = {
  "diffPathA": null,
  "diffPathB": null,
  "error": null,
  "jobType": "Placement",
  "jobs": [],
  "loading": false,
  "placementReadiness": [],
  "preflight": null,
  "preflightError": null,
  "preflightLoading": false,
  "previewCandidateId": null,
  "previewJobId": null,
  "previewPath": null,
  "routingReadiness": [],
  "submitting": false,
  "timeoutMinutes": 1
};

export function createUiAutolayoutData(): UiAutolayoutData {
  return cloneGenerated(DEFAULT_UiAutolayoutData);
}

export const DEFAULT_UiAutolayoutJobData: UiAutolayoutJobData = {
  "appliedCandidateId": null,
  "buildId": null,
  "buildTarget": "",
  "candidates": [],
  "createdAt": "",
  "displayState": "idle",
  "error": null,
  "jobId": "",
  "jobType": "",
  "layoutPath": null,
  "message": "",
  "progress": null,
  "projectRoot": "",
  "provider": "",
  "providerJobRef": null,
  "recommendedCandidateId": null,
  "selectedCandidateId": null,
  "state": "building",
  "updatedAt": ""
};

export function createUiAutolayoutJobData(): UiAutolayoutJobData {
  return cloneGenerated(DEFAULT_UiAutolayoutJobData);
}

export const DEFAULT_UiAutolayoutPreCheckItem: UiAutolayoutPreCheckItem = {
  "detail": "",
  "label": "",
  "passed": false
};

export function createUiAutolayoutPreCheckItem(): UiAutolayoutPreCheckItem {
  return cloneGenerated(DEFAULT_UiAutolayoutPreCheckItem);
}

export const DEFAULT_UiAutolayoutPreflightData: UiAutolayoutPreflightData = {
  "boardAreaMm2": null,
  "boardHeightMm": null,
  "boardWidthMm": null,
  "bottomComponentCount": 0,
  "componentAreaMm2": null,
  "componentCount": 0,
  "componentsInsideBoard": 0,
  "componentsOutsideBoard": 0,
  "connectionCount": 0,
  "connectionDensity": null,
  "layerCount": null,
  "netCount": 0,
  "padCount": 0,
  "padDensity": null,
  "placementUtilization": null,
  "recommendation": "",
  "sidedness": "",
  "stackupRisk": "",
  "topComponentCount": 0,
  "topOnlyUtilization": null
};

export function createUiAutolayoutPreflightData(): UiAutolayoutPreflightData {
  return cloneGenerated(DEFAULT_UiAutolayoutPreflightData);
}

export const DEFAULT_UiBOMComponent: UiBOMComponent = {
  "datasheet": null,
  "description": null,
  "id": "",
  "isBasic": null,
  "isPreferred": null,
  "lcsc": null,
  "manufacturer": "",
  "mpn": "",
  "package": "",
  "parameters": [],
  "quantity": 0,
  "source": null,
  "stock": null,
  "type": null,
  "unitCost": null,
  "usages": [],
  "value": ""
};

export function createUiBOMComponent(): UiBOMComponent {
  return cloneGenerated(DEFAULT_UiBOMComponent);
}

export const DEFAULT_UiBOMData: UiBOMData = {
  "buildId": null,
  "components": [],
  "error": null,
  "errorTraceback": null,
  "estimatedCost": null,
  "loading": false,
  "outOfStock": 0,
  "projectRoot": null,
  "target": null,
  "totalQuantity": 0,
  "uniqueParts": 0,
  "version": null
};

export function createUiBOMData(): UiBOMData {
  return cloneGenerated(DEFAULT_UiBOMData);
}

export const DEFAULT_UiBOMParameter: UiBOMParameter = {
  "name": "",
  "unit": null,
  "value": ""
};

export function createUiBOMParameter(): UiBOMParameter {
  return cloneGenerated(DEFAULT_UiBOMParameter);
}

export const DEFAULT_UiBOMUsage: UiBOMUsage = {
  "address": "",
  "designator": "",
  "line": null
};

export function createUiBOMUsage(): UiBOMUsage {
  return cloneGenerated(DEFAULT_UiBOMUsage);
}

export const DEFAULT_UiBlobAssetData: UiBlobAssetData = {
  "action": null,
  "contentType": null,
  "data": null,
  "error": null,
  "filename": null,
  "loading": false,
  "requestKey": ""
};

export function createUiBlobAssetData(): UiBlobAssetData {
  return cloneGenerated(DEFAULT_UiBlobAssetData);
}

export const DEFAULT_UiBuildLogRequest: UiBuildLogRequest = {
  "audience": null,
  "buildId": "",
  "count": null,
  "logLevels": null,
  "stage": null
};

export function createUiBuildLogRequest(): UiBuildLogRequest {
  return cloneGenerated(DEFAULT_UiBuildLogRequest);
}

export const DEFAULT_UiBuildsByProjectData: UiBuildsByProjectData = {
  "builds": [],
  "limit": 0,
  "loading": false,
  "projectRoot": null,
  "target": null
};

export function createUiBuildsByProjectData(): UiBuildsByProjectData {
  return cloneGenerated(DEFAULT_UiBuildsByProjectData);
}

export const DEFAULT_UiCoreStatus: UiCoreStatus = {
  "atoBinary": "",
  "coreServerPort": 0,
  "mode": "production",
  "uvPath": "",
  "version": ""
};

export function createUiCoreStatus(): UiCoreStatus {
  return cloneGenerated(DEFAULT_UiCoreStatus);
}

export const DEFAULT_UiEntryCheckData: UiEntryCheckData = {
  "entry": "",
  "fileExists": false,
  "loading": false,
  "moduleExists": false,
  "projectRoot": null,
  "targetExists": false
};

export function createUiEntryCheckData(): UiEntryCheckData {
  return cloneGenerated(DEFAULT_UiEntryCheckData);
}

export const DEFAULT_UiExtensionSettings: UiExtensionSettings = {
  "enableChat": true
};

export function createUiExtensionSettings(): UiExtensionSettings {
  return cloneGenerated(DEFAULT_UiExtensionSettings);
}

export const DEFAULT_UiFileActionData: UiFileActionData = {
  "action": "none",
  "isFolder": false,
  "path": null
};

export function createUiFileActionData(): UiFileActionData {
  return cloneGenerated(DEFAULT_UiFileActionData);
}

export const DEFAULT_UiLayoutData: UiLayoutData = {
  "error": null,
  "loading": false,
  "path": null,
  "projectRoot": null,
  "readOnly": false,
  "revision": 0,
  "target": null
};

export function createUiLayoutData(): UiLayoutData {
  return cloneGenerated(DEFAULT_UiLayoutData);
}

export const DEFAULT_UiLcscPartData: UiLcscPartData = {
  "description": null,
  "isBasic": null,
  "isPreferred": null,
  "manufacturer": null,
  "mpn": null,
  "stock": null,
  "unitCost": null
};

export function createUiLcscPartData(): UiLcscPartData {
  return cloneGenerated(DEFAULT_UiLcscPartData);
}

export const DEFAULT_UiLcscPartsData: UiLcscPartsData = {
  "loadingIds": [],
  "parts": {},
  "projectRoot": null,
  "target": null
};

export function createUiLcscPartsData(): UiLcscPartsData {
  return cloneGenerated(DEFAULT_UiLcscPartsData);
}

export const DEFAULT_UiLogEntry: UiLogEntry = {
  "atoTraceback": null,
  "audience": "user",
  "id": null,
  "level": "INFO",
  "loggerName": "",
  "message": "",
  "objects": null,
  "pythonTraceback": null,
  "sourceFile": null,
  "sourceLine": null,
  "stage": null,
  "testName": null,
  "timestamp": ""
};

export function createUiLogEntry(): UiLogEntry {
  return cloneGenerated(DEFAULT_UiLogEntry);
}

export const DEFAULT_UiLogsStreamMessage: UiLogsStreamMessage = {
  "buildId": "",
  "lastId": 0,
  "logs": [],
  "stage": null,
  "type": "logs_stream"
};

export function createUiLogsStreamMessage(): UiLogsStreamMessage {
  return cloneGenerated(DEFAULT_UiLogsStreamMessage);
}

export const DEFAULT_UiMigrationState: UiMigrationState = {
  "completed": false,
  "error": null,
  "loading": false,
  "needsMigration": false,
  "projectName": null,
  "projectRoot": null,
  "running": false,
  "stepResults": [],
  "steps": [],
  "topics": []
};

export function createUiMigrationState(): UiMigrationState {
  return cloneGenerated(DEFAULT_UiMigrationState);
}

export const DEFAULT_UiPackageDetailState: UiPackageDetailState = {
  "actionError": null,
  "details": null,
  "error": null,
  "loading": false,
  "packageId": null,
  "projectRoot": null,
  "summary": null,
  "syncProgress": null
};

export function createUiPackageDetailState(): UiPackageDetailState {
  return cloneGenerated(DEFAULT_UiPackageDetailState);
}

export const DEFAULT_UiPackageSyncProgress: UiPackageSyncProgress = {
  "completed": 0,
  "message": "",
  "stage": "",
  "total": 0
};

export function createUiPackageSyncProgress(): UiPackageSyncProgress {
  return cloneGenerated(DEFAULT_UiPackageSyncProgress);
}

export const DEFAULT_UiPartData: UiPartData = {
  "action": "idle",
  "attributes": {},
  "datasheetUrl": null,
  "description": "",
  "footprint": null,
  "identifier": "",
  "imageUrl": null,
  "importStatement": null,
  "installed": false,
  "isBasic": false,
  "isPreferred": false,
  "lcsc": null,
  "manufacturer": "",
  "mpn": "",
  "package": null,
  "path": null,
  "stock": null,
  "unitCost": null
};

export function createUiPartData(): UiPartData {
  return cloneGenerated(DEFAULT_UiPartData);
}

export const DEFAULT_UiPartDetailState: UiPartDetailState = {
  "actionError": null,
  "details": null,
  "error": null,
  "lcsc": null,
  "loading": false,
  "projectRoot": null
};

export function createUiPartDetailState(): UiPartDetailState {
  return cloneGenerated(DEFAULT_UiPartDetailState);
}

export const DEFAULT_UiPartsSearchData: UiPartsSearchData = {
  "actionError": null,
  "error": null,
  "installedOnly": false,
  "loading": false,
  "parts": [],
  "projectRoot": null,
  "query": ""
};

export function createUiPartsSearchData(): UiPartsSearchData {
  return cloneGenerated(DEFAULT_UiPartsSearchData);
}

export const DEFAULT_UiPinoutData: UiPinoutData = {
  "components": [],
  "error": null,
  "projectRoot": null,
  "target": null
};

export function createUiPinoutData(): UiPinoutData {
  return cloneGenerated(DEFAULT_UiPinoutData);
}

export const DEFAULT_UiProjectFilesData: UiProjectFilesData = {
  "files": [],
  "loading": false,
  "projectRoot": null
};

export function createUiProjectFilesData(): UiProjectFilesData {
  return cloneGenerated(DEFAULT_UiProjectFilesData);
}

export const DEFAULT_UiProjectState: UiProjectState = {
  "activeFilePath": null,
  "logViewBuildId": null,
  "logViewStage": null,
  "selectedProjectRoot": null,
  "selectedTarget": null
};

export function createUiProjectState(): UiProjectState {
  return cloneGenerated(DEFAULT_UiProjectState);
}

export const DEFAULT_UiRecentBuildsData: UiRecentBuildsData = {
  "builds": []
};

export function createUiRecentBuildsData(): UiRecentBuildsData {
  return cloneGenerated(DEFAULT_UiRecentBuildsData);
}

export const DEFAULT_UiSidebarDetails: UiSidebarDetails = {
  "migration": {
    "completed": false,
    "error": null,
    "loading": false,
    "needsMigration": false,
    "projectName": null,
    "projectRoot": null,
    "running": false,
    "stepResults": [],
    "steps": [],
    "topics": []
  },
  "package": {
    "actionError": null,
    "details": null,
    "error": null,
    "loading": false,
    "packageId": null,
    "projectRoot": null,
    "summary": null,
    "syncProgress": null
  },
  "part": {
    "actionError": null,
    "details": null,
    "error": null,
    "lcsc": null,
    "loading": false,
    "projectRoot": null
  },
  "view": "none"
};

export function createUiSidebarDetails(): UiSidebarDetails {
  return cloneGenerated(DEFAULT_UiSidebarDetails);
}

export const DEFAULT_UiStackupData: UiStackupData = {
  "error": null,
  "errorTraceback": null,
  "layerCount": 0,
  "layers": [],
  "loading": false,
  "manufacturer": null,
  "projectRoot": null,
  "stackupName": null,
  "target": null,
  "totalThicknessMm": null,
  "version": null
};

export function createUiStackupData(): UiStackupData {
  return cloneGenerated(DEFAULT_UiStackupData);
}

export const DEFAULT_UiStackupLayer: UiStackupLayer = {
  "index": 0,
  "layerType": null,
  "lossTangent": null,
  "material": null,
  "relativePermittivity": null,
  "thicknessMm": null
};

export function createUiStackupLayer(): UiStackupLayer {
  return cloneGenerated(DEFAULT_UiStackupLayer);
}

export const DEFAULT_UiStackupManufacturer: UiStackupManufacturer = {
  "country": null,
  "name": "",
  "website": null
};

export function createUiStackupManufacturer(): UiStackupManufacturer {
  return cloneGenerated(DEFAULT_UiStackupManufacturer);
}

export const DEFAULT_UiStore: UiStore = {
  "agentData": {
    "defaultModel": "",
    "lastMutation": null,
    "loaded": false,
    "sessions": []
  },
  "authState": {
    "isAuthenticated": false,
    "user": null
  },
  "autolayoutData": {
    "diffPathA": null,
    "diffPathB": null,
    "error": null,
    "jobType": "Placement",
    "jobs": [],
    "loading": false,
    "placementReadiness": [],
    "preflight": null,
    "preflightError": null,
    "preflightLoading": false,
    "previewCandidateId": null,
    "previewJobId": null,
    "previewPath": null,
    "routingReadiness": [],
    "submitting": false,
    "timeoutMinutes": 1
  },
  "blobAsset": {
    "action": null,
    "contentType": null,
    "data": null,
    "error": null,
    "filename": null,
    "loading": false,
    "requestKey": ""
  },
  "bomData": {
    "buildId": null,
    "components": [],
    "error": null,
    "errorTraceback": null,
    "estimatedCost": null,
    "loading": false,
    "outOfStock": 0,
    "projectRoot": null,
    "target": null,
    "totalQuantity": 0,
    "uniqueParts": 0,
    "version": null
  },
  "buildsByProjectData": {
    "builds": [],
    "limit": 0,
    "loading": false,
    "projectRoot": null,
    "target": null
  },
  "coreStatus": {
    "atoBinary": "",
    "coreServerPort": 0,
    "mode": "production",
    "uvPath": "",
    "version": ""
  },
  "currentBuilds": [],
  "entryCheck": {
    "entry": "",
    "fileExists": false,
    "loading": false,
    "moduleExists": false,
    "projectRoot": null,
    "targetExists": false
  },
  "extensionSettings": {
    "enableChat": true
  },
  "fileAction": {
    "action": "none",
    "isFolder": false,
    "path": null
  },
  "layoutData": {
    "error": null,
    "loading": false,
    "path": null,
    "projectRoot": null,
    "readOnly": false,
    "revision": 0,
    "target": null
  },
  "lcscPartsData": {
    "loadingIds": [],
    "parts": {},
    "projectRoot": null,
    "target": null
  },
  "packagesSummary": {
    "installedCount": 0,
    "packages": [],
    "total": 0
  },
  "partsSearch": {
    "actionError": null,
    "error": null,
    "installedOnly": false,
    "loading": false,
    "parts": [],
    "projectRoot": null,
    "query": ""
  },
  "pinoutData": {
    "components": [],
    "error": null,
    "projectRoot": null,
    "target": null
  },
  "previousBuilds": [],
  "projectFiles": {
    "files": [],
    "loading": false,
    "projectRoot": null
  },
  "projectState": {
    "activeFilePath": null,
    "logViewBuildId": null,
    "logViewStage": null,
    "selectedProjectRoot": null,
    "selectedTarget": null
  },
  "projects": [],
  "queueBuilds": [],
  "recentBuildsData": {
    "builds": []
  },
  "selectedBuild": null,
  "selectedBuildInProgress": false,
  "sidebarDetails": {
    "migration": {
      "completed": false,
      "error": null,
      "loading": false,
      "needsMigration": false,
      "projectName": null,
      "projectRoot": null,
      "running": false,
      "stepResults": [],
      "steps": [],
      "topics": []
    },
    "package": {
      "actionError": null,
      "details": null,
      "error": null,
      "loading": false,
      "packageId": null,
      "projectRoot": null,
      "summary": null,
      "syncProgress": null
    },
    "part": {
      "actionError": null,
      "details": null,
      "error": null,
      "lcsc": null,
      "loading": false,
      "projectRoot": null
    },
    "view": "none"
  },
  "stackupData": {
    "error": null,
    "errorTraceback": null,
    "layerCount": 0,
    "layers": [],
    "loading": false,
    "manufacturer": null,
    "projectRoot": null,
    "stackupName": null,
    "target": null,
    "totalThicknessMm": null,
    "version": null
  },
  "stdlibData": {
    "items": [],
    "total": 0
  },
  "structureData": {
    "error": null,
    "loading": true,
    "modules": [],
    "projectRoot": null,
    "total": 0
  },
  "variablesData": {
    "nodes": []
  }
};

export function createUiStore(): UiStore {
  return cloneGenerated(DEFAULT_UiStore);
}

export const DEFAULT_UiStructureData: UiStructureData = {
  "error": null,
  "loading": true,
  "modules": [],
  "projectRoot": null,
  "total": 0
};

export function createUiStructureData(): UiStructureData {
  return cloneGenerated(DEFAULT_UiStructureData);
}

export const DEFAULT_UiTestLogRequest: UiTestLogRequest = {
  "audience": null,
  "count": null,
  "logLevels": null,
  "testName": null,
  "testRunId": ""
};

export function createUiTestLogRequest(): UiTestLogRequest {
  return cloneGenerated(DEFAULT_UiTestLogRequest);
}

export const DEFAULT_UiVariable: UiVariable = {
  "actual": null,
  "meetsSpec": null,
  "name": "",
  "spec": null
};

export function createUiVariable(): UiVariable {
  return cloneGenerated(DEFAULT_UiVariable);
}

export const DEFAULT_UiVariableNode: UiVariableNode = {
  "children": [],
  "name": "",
  "variables": []
};

export function createUiVariableNode(): UiVariableNode {
  return cloneGenerated(DEFAULT_UiVariableNode);
}

export const DEFAULT_UiVariablesData: UiVariablesData = {
  "nodes": []
};

export function createUiVariablesData(): UiVariablesData {
  return cloneGenerated(DEFAULT_UiVariablesData);
}
