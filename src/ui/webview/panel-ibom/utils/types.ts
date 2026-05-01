import type { UiBOMComponent } from "../../../protocol/generated-types";

export interface BomGroupMember {
  designator: string;
  address: string;
  line: number | null;
}

export interface BomGroup {
  id: string;
  footprintName: string;
  value: string;
  package: string;
  quantity: number;
  members: BomGroupMember[];
  /** Enrichment from BOM data, if a matching BOM component was found. */
  bomComponent: UiBOMComponent | null;
}
