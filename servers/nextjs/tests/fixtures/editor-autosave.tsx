import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { configureStore } from '@reduxjs/toolkit';
import { Provider, useSelector } from 'react-redux';
import presentationGeneration, { setPresentationData, updateTitle } from '../../store/slices/presentationGeneration';
import undoRedo from '../../store/slices/undoRedoSlice';
import { useAutoSave } from '../../app/(presentation-generator)/presentation/hooks/useAutoSave';
import { isEditorReadOnly } from '../../app/(presentation-generator)/presentation/utils/editorCoordination';
import PresentationHeader from '../../app/(presentation-generator)/presentation/components/PresentationHeader';
import SidePanel from '../../app/(presentation-generator)/presentation/components/SidePanel';
import SlideContent from '../../app/(presentation-generator)/presentation/components/SlideContent';

const store = configureStore({ reducer: { presentationGeneration, undoRedo } });
const fixture = window as any;
const server = { revision: 1, pageRevision: 1 };
const calls: any[] = [];
const notices: any[] = [];
let setEnabled: (enabled: boolean) => void;

const data = () => store.getState().presentationGeneration.presentationData!;
const dispatch = (action: any) => flushSync(() => { store.dispatch(action); });
const nextFrame = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));

fixture.editor = {
  calls, notices, server,
  notify: (...args: any[]) => notices.push(args),
  async savePage(slide: any, expected: number) {
    calls.push({ kind: 'page', expected, actual: server.pageRevision });
    if (expected !== server.pageRevision) throw { status: 409 };
    const previousRevision = server.revision++;
    server.pageRevision++;
    return { coordination: { previousRevision, revision: server.revision,
      pageRevisions: { [slide.id]: server.pageRevision }, writable: true } };
  },
  async saveDocument(body: any) {
    if (typeof body === 'string') body = JSON.parse(body);
    calls.push({ kind: 'document', expected: body.expectedRevision, actual: server.revision });
    if (body.expectedRevision !== server.revision) throw { status: 409 };
    server.revision++;
    return { coordination: { revision: server.revision, pageRevisions: { p1: server.pageRevision }, writable: true } };
  },
  async pause(enabled: boolean) {
    flushSync(() => setEnabled(enabled));
    await nextFrame();
  },
  async editPage(text: string) {
    const current = data();
    dispatch(setPresentationData({ ...current, slides: [{ ...current.slides[0], content: { text } }] }));
    await nextFrame();
  },
  async editTitle(title: string) {
    dispatch(updateTitle(title));
    await nextFrame();
  },
  async hydrate(revision: number, pageRevision: number, writable = true) {
    Object.assign(server, { revision, pageRevision });
    dispatch(setPresentationData({ ...data(), coordination: { revision, pageRevisions: { p1: pageRevision }, writable } }));
    await nextFrame();
  },
  data,
  async mount(writable = true, controls = false) {
    dispatch(setPresentationData({
      id: 'deck-1', title: 'Test deck', language: 'English', layout: null, n_slides: 1, theme: null,
      coordination: { revision: 1, pageRevisions: { p1: 1 }, writable },
      slides: [{ id: 'p1', index: 0, presentation: 'deck-1', layout: 'blank', content: { text: 'original' } }],
    }));
    flushSync(() => createRoot(document.getElementById('root')!).render(
      <Provider store={store}><Harness controls={controls} /></Provider>
    ));
    await nextFrame();
  },
};

function Harness({ controls }: { controls: boolean }) {
  const [enabled, updateEnabled] = useState(true);
  setEnabled = updateEnabled;
  const { isSaving } = useAutoSave({ enabled, debounceMs: 20 });
  const presentation = useSelector((state: ReturnType<typeof store.getState>) => state.presentationGeneration.presentationData)!;
  if (!controls) return <div data-saving={isSaving} />;
  return <>
    <PresentationHeader presentation_id={presentation.id} isPresentationSaving={isSaving} />
    <SidePanel selectedSlide={0} onSlideClick={() => undefined} presentationId={presentation.id} loading={false} />
    <SlideContent slide={presentation.slides[0]} index={0} presentationId={presentation.id}
      editingDisabled={isEditorReadOnly(presentation, false)} />
  </>;
}
