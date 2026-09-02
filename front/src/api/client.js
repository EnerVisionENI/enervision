import axios from "axios";
import { getToken, logout } from "../auth/auth";
import router from "../router";

// En prod, le front est servi par Nginx qui proxifie /api vers le service api.
// En dev, vite.config.js proxifie /api vers http://localhost:8000.
// VITE_API_URL permet de forcer une URL absolue si besoin.
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "/api/v1",
});

// Ajoute le token à chaque requête
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Déconnexion auto si le token est invalide/expiré
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      logout();
      router.push("/login");
    }
    return Promise.reject(error);
  }
);

export default api;
