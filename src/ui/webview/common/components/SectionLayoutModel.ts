export class SectionLayoutModel<T extends string> {
  key: T;
  expanded: boolean;
  headerHeight: number;
  targetHeight: number;
  actualHeight: number;

  constructor({
    key,
    expanded,
    headerHeight,
    targetHeight,
    actualHeight,
  }: {
    key: T;
    expanded: boolean;
    headerHeight: number;
    targetHeight: number;
    actualHeight?: number;
  }) {
    this.key = key;
    this.expanded = expanded;
    this.headerHeight = headerHeight;
    this.targetHeight = targetHeight;
    this.actualHeight = actualHeight ?? (expanded ? targetHeight : 0);
  }

  withActualHeight(actualHeight: number) {
    return new SectionLayoutModel<T>({
      key: this.key,
      expanded: this.expanded,
      headerHeight: this.headerHeight,
      targetHeight: this.targetHeight,
      actualHeight,
    });
  }

  get preferredHeight() {
    return this.expanded ? this.targetHeight : 0;
  }

  get isVisuallyExpanded() {
    return this.expanded && this.actualHeight > 0.5;
  }

  /**
   * Solve body heights for a vertical stack of rigid headers and
   * compressible section bodies.
   *
   * Available body space is distributed proportionally by
   * preferredHeight (targetHeight when expanded, 0 when collapsed).
   * Sections always fill all available space -- no gaps.
   * targetHeight is never modified.
   */
  static solve<T extends string>(
    models: SectionLayoutModel<T>[],
    availableHeight: number,
  ) {
    const totalHeaderHeight = models.reduce((sum, m) => sum + m.headerHeight, 0);
    const availableBodyHeight = Math.max(0, availableHeight - totalHeaderHeight);

    const totalPreferred = models.reduce((sum, m) => sum + m.preferredHeight, 0);
    const scale = totalPreferred > 0 ? availableBodyHeight / totalPreferred : 0;
    return models.map((m) =>
      m.withActualHeight(m.preferredHeight * scale),
    );
  }
}
