import { useEffect, useMemo } from "react";

export function PhotoPicker({
  files,
  onChange,
  max = 3,
  hint,
}: {
  files: File[];
  onChange: (files: File[]) => void;
  max?: number;
  hint?: string;
}) {
  const urls = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
  useEffect(() => () => urls.forEach((u) => URL.revokeObjectURL(u)), [urls]);

  return (
    <div>
      <div className="photos">
        {files.map((f, i) => (
          <div className="photo" key={`${f.name}-${i}`}>
            <img src={urls[i]} alt={`Photo ${i + 1}`} />
            <button type="button" aria-label={`Remove photo ${i + 1}`} onClick={() => onChange(files.filter((_, j) => j !== i))}>
              ×
            </button>
          </div>
        ))}
        {files.length < max && (
          <label className="photo-add">
            <span style={{ fontSize: "1.6rem" }}>＋</span>
            Add photo
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              capture="environment"
              multiple
              onChange={(e) => {
                const picked = Array.from(e.target.files ?? []);
                onChange([...files, ...picked].slice(0, max));
                e.target.value = "";
              }}
            />
          </label>
        )}
      </div>
      {hint && <small className="muted">{hint}</small>}
    </div>
  );
}
