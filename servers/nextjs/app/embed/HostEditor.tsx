"use client";
import { configureHostEditor } from "@/utils/host-editor";
import PresentationPage from "../(presentation-generator)/presentation/components/PresentationPage";

export default function HostEditor({ id, base }: { id: string; base: string }) {
  configureHostEditor(id, base);
  return <PresentationPage presentation_id={id} hosted />;
}
