export interface CodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  language: "yaml" | "python";
  readOnly?: boolean;
  height?: string;
  className?: string;
}
