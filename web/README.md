# React + TypeScript + Vite + shadcn/ui

This is a template for a new Vite project with React, TypeScript, and shadcn/ui.

## Local preview

Run the web app with an in-memory mock API:

```bash
npm run dev:mock
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Project edits, messages,
search, reset, delete, dictation, and logout are mocked and reset when the
process stops.

## Adding components

To add components to your app, run the following command:

```bash
npx shadcn@latest add button
```

This will place the ui components in the `src/components` directory.

## Using components

To use the components in your app, import them as follows:

```tsx
import { Button } from "@/components/ui/button"
```
