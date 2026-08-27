/**
 * Dashboard Screen for Mobile App
 */

import React from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';

const DashboardScreen = ({ navigation }) => {
  // Mock data - in production, fetch from API
  const stats = {
    totalMatches: 15,
    totalSavings: '₹45,000',
    activeListings: 23,
    pendingOrders: 3,
  };

  const recentMatches = [
    { id: 1, material: 'metal_shavings', savings: '₹5,000', partner: 'Shivam Metal Works' },
    { id: 2, material: 'bagasse', savings: '₹3,500', partner: 'Om Sugar Mill' },
    { id: 3, material: 'fabric_offcuts', savings: '₹2,800', partner: 'Sri Textiles' },
  ];

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Dashboard</Text>
        <Text style={styles.subtitle}>Welcome back!</Text>
      </View>

      {/* Stats Cards */}
      <View style={styles.statsContainer}>
        <View style={styles.statCard}>
          <Ionicons name="people" size={24} color="#10b981" />
          <Text style={styles.statValue}>{stats.totalMatches}</Text>
          <Text style={styles.statLabel}>Matches</Text>
        </View>
        <View style={styles.statCard}>
          <Ionicons name="cash" size={24} color="#f59e0b" />
          <Text style={styles.statValue}>{stats.totalSavings}</Text>
          <Text style={styles.statLabel}>Savings</Text>
        </View>
        <View style={styles.statCard}>
          <Ionicons name="list" size={24} color="#3b82f6" />
          <Text style={styles.statValue}>{stats.activeListings}</Text>
          <Text style={styles.statLabel}>Listings</Text>
        </View>
        <View style={styles.statCard}>
          <Ionicons name="receipt" size={24} color="#8b5cf6" />
          <Text style={styles.statValue}>{stats.pendingOrders}</Text>
          <Text style={styles.statLabel}>Orders</Text>
        </View>
      </View>

      {/* Recent Matches */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Recent Matches</Text>
        {recentMatches.map((match) => (
          <TouchableOpacity
            key={match.id}
            style={styles.matchCard}
            onPress={() => navigation.navigate('ListingDetail')}
          >
            <View style={styles.matchInfo}>
              <Text style={styles.matchMaterial}>{match.material.replace('_', ' ')}</Text>
              <Text style={styles.matchPartner}>{match.partner}</Text>
            </View>
            <View style={styles.matchSavings}>
              <Text style={styles.savingsText}>{match.savings}</Text>
            </View>
          </TouchableOpacity>
        ))}
      </View>

      {/* Quick Actions */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Quick Actions</Text>
        <TouchableOpacity
          style={styles.actionButton}
          onPress={() => navigation.navigate('Listings')}
        >
          <Ionicons name="add-circle" size={20} color="#10b981" />
          <Text style={styles.actionText}>Post New Listing</Text>
        </TouchableOpacity>
        <TouchableOpacity
          style={styles.actionButton}
          onPress={() => navigation.navigate('Search')}
        >
          <Ionicons name="search" size={20} color="#10b981" />
          <Text style={styles.actionText}>Search Materials</Text>
        </TouchableOpacity>
      </View>
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#111827',
  },
  header: {
    padding: 24,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 4,
  },
  subtitle: {
    fontSize: 16,
    color: '#9ca3af',
  },
  statsContainer: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    paddingHorizontal: 16,
    marginBottom: 24,
  },
  statCard: {
    backgroundColor: '#1f2937',
    borderRadius: 12,
    padding: 16,
    width: '45%',
    margin: '2.5%',
    alignItems: 'center',
  },
  statValue: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#ffffff',
    marginTop: 8,
  },
  statLabel: {
    fontSize: 12,
    color: '#9ca3af',
    marginTop: 4,
  },
  section: {
    paddingHorizontal: 16,
    marginBottom: 24,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 12,
  },
  matchCard: {
    backgroundColor: '#1f2937',
    borderRadius: 8,
    padding: 16,
    marginBottom: 8,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  matchInfo: {
    flex: 1,
  },
  matchMaterial: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 4,
  },
  matchPartner: {
    fontSize: 14,
    color: '#9ca3af',
  },
  matchSavings: {
    backgroundColor: '#065f46',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 8,
  },
  savingsText: {
    color: '#10b981',
    fontWeight: 'bold',
  },
  actionButton: {
    backgroundColor: '#1f2937',
    borderRadius: 8,
    padding: 16,
    marginBottom: 8,
    flexDirection: 'row',
    alignItems: 'center',
  },
  actionText: {
    color: '#ffffff',
    fontSize: 16,
    marginLeft: 12,
  },
});

export default DashboardScreen;