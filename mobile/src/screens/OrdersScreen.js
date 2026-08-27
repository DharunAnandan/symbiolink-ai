/**
 * Orders Screen for Mobile App
 */

import React, { useState, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  FlatList,
  TouchableOpacity,
  ActivityIndicator,
  RefreshControl,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { Ionicons } from '@expo/vector-icons';
import api from '../services/api';

const STATUS_LABELS = {
  placed: 'Placed',
  confirmed: 'Confirmed',
  picked_up: 'Picked up',
  delivered: 'Delivered',
  completed: 'Completed',
  cancelled: 'Cancelled',
};

function titleCase(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

const OrdersScreen = ({ navigation }) => {
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    setError(null);
    try {
      const res = await api.getOrders();
      if (res.success) {
        setOrders(res.orders || []);
      } else {
        setError(res.error || 'Could not load orders.');
      }
    } catch (e) {
      setError(e.message || 'Could not reach the server.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // Refetch every time the tab regains focus, e.g. coming back from
  // OrderDetailScreen after paying, so the list badge reflects the new state.
  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const renderItem = ({ item }) => {
    const isPaid = item.payment_status === 'paid';
    return (
      <TouchableOpacity
        style={styles.orderCard}
        onPress={() => navigation.navigate('OrderDetail', { orderId: item.id })}
      >
        <View style={styles.orderCardHeader}>
          <Text style={styles.orderId}>{item.id}</Text>
          <View style={styles.badgeRow}>
            <View style={styles.statusBadge}>
              <Text style={styles.statusBadgeText}>{STATUS_LABELS[item.status] || item.status}</Text>
            </View>
            <View style={[styles.statusBadge, isPaid ? styles.badgePaid : styles.badgeUnpaid]}>
              <Text style={styles.statusBadgeText}>{isPaid ? 'Paid' : 'Unpaid'}</Text>
            </View>
          </View>
        </View>
        <Text style={styles.material}>{titleCase(item.material)} &middot; {Math.round(item.qty_kg)}kg</Text>
        <Text style={styles.parties}>{item.buyer_name} &rarr; {item.seller_name}</Text>
      </TouchableOpacity>
    );
  };

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color="#10b981" />
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.title}>Orders</Text>
        <Text style={styles.subtitle}>{orders.length} order{orders.length === 1 ? '' : 's'}</Text>
      </View>

      {error ? (
        <View style={styles.centered}>
          <Ionicons name="alert-circle-outline" size={32} color="#f87171" />
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : (
        <FlatList
          data={orders}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          contentContainerStyle={styles.listContent}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(true)} tintColor="#10b981" />}
          ListEmptyComponent={<Text style={styles.emptyText}>No orders yet.</Text>}
        />
      )}
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#111827',
  },
  centered: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  header: {
    padding: 24,
    paddingBottom: 12,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 4,
  },
  subtitle: {
    fontSize: 14,
    color: '#9ca3af',
  },
  errorText: {
    color: '#f87171',
    fontSize: 14,
    marginTop: 10,
    textAlign: 'center',
  },
  emptyText: {
    color: '#9ca3af',
    fontSize: 14,
    textAlign: 'center',
    marginTop: 40,
  },
  listContent: {
    paddingHorizontal: 16,
    paddingBottom: 24,
  },
  orderCard: {
    backgroundColor: '#1f2937',
    borderRadius: 12,
    padding: 16,
    marginBottom: 10,
  },
  orderCardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  orderId: {
    color: '#ffffff',
    fontSize: 15,
    fontWeight: 'bold',
  },
  badgeRow: {
    flexDirection: 'row',
  },
  statusBadge: {
    backgroundColor: '#374151',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
    marginLeft: 6,
  },
  statusBadgeText: {
    color: '#e5e7eb',
    fontSize: 10,
    fontWeight: 'bold',
  },
  badgePaid: { backgroundColor: '#065f46' },
  badgeUnpaid: { backgroundColor: '#78350f' },
  material: {
    color: '#d1d5db',
    fontSize: 13,
    marginBottom: 4,
  },
  parties: {
    color: '#9ca3af',
    fontSize: 12,
  },
});

export default OrdersScreen;
