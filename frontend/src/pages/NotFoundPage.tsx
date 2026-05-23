import { Link } from "react-router-dom";

export default function NotFoundPage() {
  return (
    <div className="min-h-dvh flex items-center justify-center p-6">
      <div className="text-center space-y-3">
        <p className="font-mono text-sm text-muted-foreground">404 · not found</p>
        <h1 className="text-2xl font-semibold tracking-tight">
          That route doesn't exist
        </h1>
        <Link to="/" className="inline-block text-primary hover:underline">
          ← Back to start
        </Link>
      </div>
    </div>
  );
}
