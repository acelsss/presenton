'use client'
import { useEffect, useRef, useCallback, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { notify } from '@/components/ui/sonner';
import { RootState } from '@/store/store';
import { PresentationGenerationApi } from '../../services/api/presentation-generation';
import { addToHistory } from '@/store/slices/undoRedoSlice';
import type { PresentationData } from '@/store/slices/presentationGeneration';
import type { Slide } from '../../types/slide';
import type { AutoSaveSnapshot } from '../utils/autoSaveDiff';
import { acknowledgeEditorPage, editorDocumentArguments, editorMetadataChanges, type EditorCoordination } from '../utils/editorCoordination';
import {
    createAutoSaveSnapshot,
    fingerprintValue,
    getAutoSaveChanges,
} from '../utils/autoSaveDiff';

interface UseAutoSaveOptions {
    debounceMs?: number;
    enabled?: boolean;
}

export const useAutoSave = ({
    debounceMs = 1000,
    enabled = true,
}: UseAutoSaveOptions = {}) => {
   
    const dispatch = useDispatch();
    const { presentationData, isStreaming, isLoading, isLayoutLoading } = useSelector(
        (state: RootState) => state.presentationGeneration
    );

    const saveTimeoutRef = useRef<NodeJS.Timeout | null>(null);
    const acknowledgedDataRef = useRef<AutoSaveSnapshot | null>(null);
    const coordinationRef = useRef<EditorCoordination | null>(null);
    const conflictRef = useRef(false);
    const latestDataRef = useRef<PresentationData | null>(presentationData);
    const autoSavePausedRef = useRef(true);
    const wasAutoSavePausedRef = useRef(false);
    const pendingSaveRef = useRef(false);
    const saveLatestRef = useRef<() => Promise<void>>(async () => undefined);
    const isSavingRef = useRef(false);
    const [isSaving, setIsSaving] = useState<boolean>(false);

    const autoSavePaused =
        !enabled || isStreaming || isLoading || isLayoutLoading || presentationData?.coordination?.writable === false;

    useEffect(() => {
        latestDataRef.current = presentationData;
        autoSavePausedRef.current = autoSavePaused;
    }, [presentationData, autoSavePaused]);

    const saveLatest = useCallback(async () => {
        const data = latestDataRef.current;
        if (!data || autoSavePausedRef.current || conflictRef.current) return;
        if (isSavingRef.current) {
            pendingSaveRef.current = true;
            return;
        }

        const acknowledged = acknowledgedDataRef.current;
        if (!acknowledged || acknowledged.presentationId !== data.id) {
            acknowledgedDataRef.current = createAutoSaveSnapshot(data);
            coordinationRef.current = data.coordination ?? null;
            return;
        }

        const changes = getAutoSaveChanges(acknowledged, data);
        if (
            !changes.structuralChange &&
            !changes.metadataChanged &&
            changes.changedSlides.length === 0
        ) return;

        try {
            isSavingRef.current = true;
            setIsSaving(true);
            console.log('🔄 Auto-saving presentation data...');

            if (changes.structuralChange) {
                // Serialize once after the debounce window. The API accepts the
                // serialized body and avoids a second whole-deck stringify.
                const result = await PresentationGenerationApi.updatePresentationContent(
                    JSON.stringify(coordinationRef.current ? {
                        id: data.id, n_slides: data.slides.length, slides: data.slides,
                        ...editorMetadataChanges(data, acknowledged.metadataFingerprint),
                        ...editorDocumentArguments(coordinationRef.current),
                    } : data)
                );
                if (result.coordination) coordinationRef.current = result.coordination;
                acknowledgedDataRef.current = createAutoSaveSnapshot(data);
            } else {
                let firstError: unknown = null;
                const nextAcknowledged: AutoSaveSnapshot = {
                    ...acknowledged,
                    slideFingerprints: { ...acknowledged.slideFingerprints },
                };

                if (changes.metadataChanged) {
                    try {
                        const result = await PresentationGenerationApi.updatePresentationContent({
                            id: data.id,
                            ...(coordinationRef.current
                                ? editorMetadataChanges(data, acknowledged.metadataFingerprint)
                                : { title: data.title, theme: data.theme }),
                            ...editorDocumentArguments(coordinationRef.current),
                        });
                        if (result.coordination) {
                            // Metadata-only saves do not acknowledge other page contents.
                            coordinationRef.current = { ...coordinationRef.current!, revision: result.coordination.revision };
                        }
                        nextAcknowledged.metadataFingerprint = fingerprintValue({
                            title: data.title,
                            theme: data.theme,
                        });
                        acknowledgedDataRef.current = nextAcknowledged;
                    } catch (error) {
                        firstError = error;
                    }
                }

                for (const slide of changes.changedSlides) {
                    try {
                        const result = await PresentationGenerationApi.updatePresentationSlide(
                            slide as Slide,
                            coordinationRef.current?.pageRevisions[slide.id]
                        );
                        if (result.coordination && coordinationRef.current) {
                            coordinationRef.current = acknowledgeEditorPage(coordinationRef.current, result.coordination);
                        }
                        nextAcknowledged.slideFingerprints[slide.id] =
                            fingerprintValue(slide);
                        acknowledgedDataRef.current = nextAcknowledged;
                    } catch (error) {
                        firstError ??= error;
                    }
                }

                if (firstError) throw firstError;
                acknowledgedDataRef.current = createAutoSaveSnapshot(data);
            }

            console.log('✅ Auto-save successful');
        } catch (error) {
            console.error('❌ Auto-save failed:', error);
            if (coordinationRef.current && error && typeof error === 'object' &&
                'status' in error && (error.status === 409 || error.status === 428)) {
                conflictRef.current = true;
                pendingSaveRef.current = false;
                notify.error('Changes not saved', 'This presentation changed elsewhere. Keep a copy of your edits before reopening it.', {
                    id: `presentation-save-conflict-${data.id}`, duration: Infinity,
                });
            }
        } finally {
            isSavingRef.current = false;
            setIsSaving(false);

            if (pendingSaveRef.current && !autoSavePausedRef.current && !conflictRef.current) {
                pendingSaveRef.current = false;
                saveTimeoutRef.current = setTimeout(() => {
                    void saveLatestRef.current();
                }, 250);
            }
        }
    }, []);

    useEffect(() => {
        saveLatestRef.current = saveLatest;
    }, [saveLatest]);

    // Effect to trigger auto-save when presentation data changes
    useEffect(() => {
        if (!presentationData) return;

        if (autoSavePaused) {
            // Changes arriving while editing is paused are server-originated
            // hydration/streaming updates and are already persisted.
            wasAutoSavePausedRef.current = true;
            pendingSaveRef.current = false;
            if (!isSavingRef.current) {
                acknowledgedDataRef.current = createAutoSaveSnapshot(presentationData);
                coordinationRef.current = presentationData.coordination ?? null;
                conflictRef.current = false;
            }
            if (saveTimeoutRef.current) {
                clearTimeout(saveTimeoutRef.current);
                saveTimeoutRef.current = null;
            }
            return;
        }

        // History is updated immediately from immutable Redux snapshots. It is
        // independent from network debounce, so even the first edit can undo.
        dispatch(addToHistory({
            slides: presentationData.slides,
            actionType: "AUTO_SAVE"
        }));

        if (wasAutoSavePausedRef.current) {
            // The final streaming/loading payload can land in the same render
            // that flips editing back on. Treat that first active snapshot as
            // already persisted instead of issuing slide updates for it.
            wasAutoSavePausedRef.current = false;
            acknowledgedDataRef.current = createAutoSaveSnapshot(presentationData);
            coordinationRef.current = presentationData.coordination ?? null;
            conflictRef.current = false;
            if (saveTimeoutRef.current) {
                clearTimeout(saveTimeoutRef.current);
                saveTimeoutRef.current = null;
            }
            return;
        }

        if (
            !acknowledgedDataRef.current ||
            acknowledgedDataRef.current.presentationId !== presentationData.id
        ) {
            acknowledgedDataRef.current = createAutoSaveSnapshot(presentationData);
            coordinationRef.current = presentationData.coordination ?? null;
            conflictRef.current = false;
            return;
        }
        
        if (saveTimeoutRef.current) {
            clearTimeout(saveTimeoutRef.current);
        }
        saveTimeoutRef.current = setTimeout(() => {
            void saveLatestRef.current();
        }, debounceMs);
       
        // Cleanup timeout on unmount
        return () => {
            if (saveTimeoutRef.current) {
                clearTimeout(saveTimeoutRef.current);
            }
        };
    }, [
        presentationData,
        autoSavePaused,
        debounceMs,
        dispatch,
    ]);
    
    return {
        isSaving,
    };
};
