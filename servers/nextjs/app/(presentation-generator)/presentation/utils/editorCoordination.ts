import type { PresentationData } from "@/store/slices/presentationGeneration";

export type EditorCoordination = NonNullable<PresentationData["coordination"]>;

export function editorDocumentArguments(coordination?: EditorCoordination | null) {
  return coordination ? {
    expectedRevision: coordination.revision,
    expectedPageRevisions: coordination.pageRevisions,
  } : {};
}

export function editorMetadataChanges(data: PresentationData, acknowledged: string) {
  const previous = JSON.parse(acknowledged);
  return {
    ...(previous.title !== data.title ? { title: data.title } : {}),
    ...(JSON.stringify(previous.theme) !== JSON.stringify(data.theme) ? { theme: data.theme } : {}),
  };
}

export function acknowledgeEditorPage(
  previous: EditorCoordination,
  receipt: EditorCoordination,
): EditorCoordination {
  return {
    ...previous,
    // A page save may observe other writers' changes. It does not refresh the
    // browser's other pages/metadata, so never bless that unseen deck version.
    revision: previous.revision === receipt.previousRevision
      ? receipt.revision : previous.revision,
    pageRevisions: { ...previous.pageRevisions, ...receipt.pageRevisions },
  };
}
