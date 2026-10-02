/** 路由表。 */

import { Navigate, Route, Routes } from 'react-router-dom';

import AppLayout from '@/layouts/AppLayout';
import { RequireAuth, RequireGuest, RequireRole } from '@/routes/guards';

import LoginPage from '@/pages/LoginPage';
import RegisterPage from '@/pages/RegisterPage';
import TodayPage from '@/pages/TodayPage';
import EventsPage from '@/pages/EventsPage';
import AnalysisPage from '@/pages/AnalysisPage';
import FamilyPage from '@/pages/FamilyPage';
import FamilyCardsPage from '@/pages/FamilyCardsPage';
import ConsultationsPage from '@/pages/ConsultationsPage';
import ConsultantWorkspacePage from '@/pages/ConsultantWorkspacePage';
import SettingsPage from '@/pages/SettingsPage';
import AdminOverviewPage from '@/pages/AdminOverviewPage';
import AdminUsersPage from '@/pages/AdminUsersPage';
import AdminRuntimePage from '@/pages/AdminRuntimePage';
import NotFoundPage from '@/pages/NotFoundPage';

export default function AppRoutes() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <RequireGuest>
            <LoginPage />
          </RequireGuest>
        }
      />
      <Route
        path="/register"
        element={
          <RequireGuest>
            <RegisterPage />
          </RequireGuest>
        }
      />

      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/today" replace />} />

        <Route
          path="/today"
          element={
            <RequireRole roles={['merchant', 'admin']}>
              <TodayPage />
            </RequireRole>
          }
        />
        <Route
          path="/events"
          element={
            <RequireRole roles={['merchant', 'admin']}>
              <EventsPage />
            </RequireRole>
          }
        />
        <Route
          path="/analysis"
          element={
            <RequireRole roles={['merchant', 'admin']}>
              <AnalysisPage />
            </RequireRole>
          }
        />
        <Route
          path="/family"
          element={
            <RequireRole roles={['merchant', 'admin']}>
              <FamilyPage />
            </RequireRole>
          }
        />
        <Route path="/family/cards" element={<FamilyCardsPage />} />
        <Route
          path="/consultations"
          element={
            <RequireRole roles={['merchant', 'admin']}>
              <ConsultationsPage />
            </RequireRole>
          }
        />
        <Route path="/consultations/:caseId" element={<ConsultationsPage />} />

        <Route
          path="/consultant"
          element={
            <RequireRole roles={['consultant', 'admin']}>
              <ConsultantWorkspacePage />
            </RequireRole>
          }
        />
        <Route
          path="/consultant/records"
          element={
            <RequireRole roles={['consultant', 'admin']}>
              <ConsultantWorkspacePage onlyRecords />
            </RequireRole>
          }
        />

        <Route
          path="/admin"
          element={
            <RequireRole roles={['admin']}>
              <AdminOverviewPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/users"
          element={
            <RequireRole roles={['admin']}>
              <AdminUsersPage />
            </RequireRole>
          }
        />
        <Route
          path="/admin/runtime"
          element={
            <RequireRole roles={['admin']}>
              <AdminRuntimePage />
            </RequireRole>
          }
        />

        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/settings/*" element={<SettingsPage />} />
      </Route>

      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
