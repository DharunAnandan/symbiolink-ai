/**
 * Authentication Context for React Native App
 * Manages user authentication state and provides auth methods
 */

import React, { createContext, useContext, useState, useEffect } from 'react';
import api from '../services/api';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check for stored token/user on app start
    loadStoredAuth();
  }, []);

  const loadStoredAuth = async () => {
    try {
      // In a real app, load from secure storage
      const storedUser = await localStorage.getItem('user');
      const storedToken = await localStorage.getItem('token');
      
      if (storedUser && storedToken) {
        setUser(JSON.parse(storedUser));
        api.setToken(storedToken);
      }
    } catch (error) {
      console.error('Error loading auth:', error);
    } finally {
      setLoading(false);
    }
  };

  const login = async (username, password) => {
    try {
      // For demo purposes, we'll simulate a successful login
      // In production, this would call api.login()
      
      const mockUser = {
        id: 1,
        username: username,
        email: `${username}@symbiolink.demo`,
        role: 'unit',
        unit_id: 'U1',
      };
      
      const mockToken = 'mock-jwt-token';
      
      setUser(mockUser);
      api.setToken(mockToken);
      
      // Store credentials
      await localStorage.setItem('user', JSON.stringify(mockUser));
      await localStorage.setItem('token', mockToken);
      
      return { success: true };
    } catch (error) {
      return { success: false, error: error.message };
    }
  };

  const register = async (userData) => {
    try {
      // In production, call api.register(userData)
      return { success: true };
    } catch (error) {
      return { success: false, error: error.message };
    }
  };

  const logout = async () => {
    try {
      setUser(null);
      api.setToken(null);
      await localStorage.removeItem('user');
      await localStorage.removeItem('token');
    } catch (error) {
      console.error('Error during logout:', error);
    }
  };

  const value = {
    user,
    loading,
    login,
    register,
    logout,
    isAuthenticated: !!user,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};