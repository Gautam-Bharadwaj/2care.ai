import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider, createBrowserRouter } from "react-router-dom";

import "./index.css";
import SiteLayout from "@/layouts/SiteLayout";
import BookingPage from "@/pages/BookingPage";
import TracePage from "@/pages/debug/TracePage";
import OpsPage from "@/pages/debug/OpsPage";
import NotFoundPage from "@/pages/NotFoundPage";

const router = createBrowserRouter([
  {
    element: <SiteLayout />,
    children: [
      { path: "/", element: <BookingPage /> },
      { path: "/debug/trace/:sessionId", element: <TracePage /> },
      { path: "/debug/trace", element: <TracePage /> },
      { path: "/debug/ops", element: <OpsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
