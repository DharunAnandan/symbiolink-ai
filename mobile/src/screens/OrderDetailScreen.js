/**
 * Order Detail Screen for Mobile App
 *
 * Shows one order's summary + cost breakdown, lets the buyer pay for it
 * (mirrors the web app's review-&-pay flow in templates/_pay_script.html),
 * and lets them report a problem once it's paid.
 *
 * NOTE: see the header comment in src/services/api.js -- @login_required
 * backend routes expect a Flask-Login session cookie, which this fetch-based
 * client doesn't carry yet, so the API calls below are correct but won't
 * authenticate against a real deployment until that gap is closed.
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  StyleSheet,
  ScrollView,
  TouchableOpacity,
  ActivityIndicator,
  TextInput,
  Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import api from '../services/api';

const STATUS_LABELS = {
  placed: 'Placed',
  confirmed: 'Confirmed by seller',
  picked_up: 'Picked up',
  delivered: 'Delivered',
  completed: 'Completed & rated',
  cancelled: 'Cancelled',
};

function formatRs(amount) {
  return `Rs. ${Math.round(amount || 0).toLocaleString('en-IN')}`;
}

function titleCase(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

const OrderDetailScreen = ({ route }) => {
  const orderId = route?.params?.orderId;

  const [order, setOrder] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [paying, setPaying] = useState(false);
  const [payError, setPayError] = useState(null);

  const [showDisputeForm, setShowDisputeForm] = useState(false);
  const [disputeReason, setDisputeReason] = useState('');
  const [submittingDispute, setSubmittingDispute] = useState(false);

  const loadOrder = useCallback(async () => {
    if (!orderId) {
      setError('No order selected.');
      setLoading(false);
      return;
    }
    setError(null);
    try {
      const res = await api.getOrderDetail(orderId);
      if (res.success) {
        setOrder(res.order);
      } else {
        setError(res.error || 'Could not load this order.');
      }
    } catch (e) {
      setError(e.message || 'Could not reach the server.');
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    loadOrder();
  }, [loadOrder]);

  const handlePay = async () => {
    setPayError(null);
    setPaying(true);
    try {
      const createRes = await api.createPaymentOrder(orderId);
      if (!createRes.success) {
        setPayError(createRes.error || 'Could not start payment.');
        setPaying(false);
        return;
      }

      if (createRes.key_id) {
        // Live Razorpay keys are configured on the backend, but opening the
        // real Checkout UI on-device needs the react-native-razorpay native
        // module, which isn't part of this Expo-managed scaffold yet (it
        // requires a custom dev client / bare workflow, not Expo Go). Rather
        // than fake a native checkout, surface that clearly instead of
        // silently doing nothing.
        Alert.alert(
          'Live payments not wired on mobile yet',
          'The backend has real Razorpay keys configured, but this screen only supports the simulated payment flow so far -- opening the real Checkout UI needs the react-native-razorpay package (requires a custom dev client, not available in Expo Go).'
        );
        setPaying(false);
        return;
      }

      // No live keys configured -- payment_service.py runs in simulation mode
      // (same fallback the web app uses), so verify immediately with a
      // simulated payment id instead of opening a real Checkout screen.
      const simulatedPaymentId = `pay_sim_${Math.random().toString(36).slice(2, 10)}`;
      const verifyRes = await api.verifyPayment({
        razorpayOrderId: createRes.order.id,
        razorpayPaymentId: simulatedPaymentId,
        razorpaySignature: 'simulated',
      });

      if (verifyRes.success) {
        await loadOrder();
      } else {
        setPayError(verifyRes.error || 'Payment verification failed.');
      }
    } catch (e) {
      setPayError(e.message || 'Something went wrong starting the payment.');
    } finally {
      setPaying(false);
    }
  };

  const handleSubmitDispute = async () => {
    if (!disputeReason.trim()) return;
    setSubmittingDispute(true);
    try {
      const res = await api.raiseDispute(orderId, disputeReason.trim());
      if (res.success !== false) {
        setShowDisputeForm(false);
        setDisputeReason('');
        await loadOrder();
      } else {
        Alert.alert('Could not submit', res.error || 'Please try again.');
      }
    } catch (e) {
      Alert.alert('Could not submit', e.message || 'Please try again.');
    } finally {
      setSubmittingDispute(false);
    }
  };

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color="#10b981" />
      </View>
    );
  }

  if (error || !order) {
    return (
      <View style={styles.centered}>
        <Ionicons name="alert-circle-outline" size={40} color="#f87171" />
        <Text style={styles.errorText}>{error || 'Order not found.'}</Text>
        <TouchableOpacity style={styles.retryButton} onPress={loadOrder}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const isPaid = order.payment_status === 'paid';
  const isRefunded = order.payment_status === 'refunded';
  const hasOpenDispute = order.dispute_status === 'open';
  const disputeResolved = order.dispute_status === 'resolved_refunded' || order.dispute_status === 'resolved_rejected';

  return (
    <ScrollView style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.orderId}>{order.id}</Text>
        <View style={styles.badgeRow}>
          <View style={styles.statusBadge}>
            <Text style={styles.statusBadgeText}>{STATUS_LABELS[order.status] || order.status}</Text>
          </View>
          <View style={[styles.statusBadge, isPaid ? styles.badgePaid : isRefunded ? styles.badgeRefunded : styles.badgeUnpaid]}>
            <Text style={styles.statusBadgeText}>
              {isPaid ? '✅ Paid' : isRefunded ? '↩ Refunded' : '💳 Unpaid'}
            </Text>
          </View>
        </View>
      </View>

      <View style={styles.card}>
        <View style={styles.materialRow}>
          <Ionicons name="business-outline" size={22} color="#10b981" />
          <View style={{ marginLeft: 12 }}>
            <Text style={styles.materialName}>{titleCase(order.material)}</Text>
            <Text style={styles.materialQty}>{Math.round(order.qty_kg)} kg</Text>
          </View>
        </View>

        <View style={styles.partiesRow}>
          <View style={{ flex: 1 }}>
            <Text style={styles.partyLabel}>BUYER</Text>
            <Text style={styles.partyName}>{order.buyer_name}</Text>
          </View>
          <Ionicons name="arrow-forward" size={16} color="#10b981" />
          <View style={{ flex: 1, alignItems: 'flex-end' }}>
            <Text style={styles.partyLabel}>SELLER</Text>
            <Text style={styles.partyName}>{order.seller_name}</Text>
          </View>
        </View>

        {order.payment_amount != null && (
          <View style={styles.amountRow}>
            <Text style={styles.amountLabel}>Amount</Text>
            <Text style={styles.amountValue}>{formatRs(order.payment_amount)}</Text>
          </View>
        )}

        {order.co2_saved_kg ? (
          <Text style={styles.co2Note}>
            🌱 Diverting this avoids an estimated {Math.round(order.co2_saved_kg).toLocaleString('en-IN')}kg CO2e.
          </Text>
        ) : null}
      </View>

      {!isPaid && !isRefunded && (
        <View style={styles.card}>
          <TouchableOpacity
            style={[styles.payButton, paying && styles.payButtonDisabled]}
            onPress={handlePay}
            disabled={paying}
          >
            {paying ? (
              <ActivityIndicator color="#06120d" />
            ) : (
              <Text style={styles.payButtonText}>💳 Pay for this order</Text>
            )}
          </TouchableOpacity>
          {payError ? <Text style={styles.errorTextSmall}>{payError}</Text> : null}
        </View>
      )}

      {isPaid && (
        <View style={styles.card}>
          {hasOpenDispute ? (
            <View style={styles.disputeNotice}>
              <Ionicons name="time-outline" size={18} color="#fbbf24" />
              <Text style={styles.disputeNoticeText}>Dispute awaiting admin review</Text>
            </View>
          ) : disputeResolved ? (
            <View style={styles.disputeNotice}>
              <Ionicons name="checkmark-circle-outline" size={18} color="#38bdf8" />
              <Text style={styles.disputeNoticeText}>
                Dispute {order.dispute_status === 'resolved_refunded' ? 'resolved: refunded' : 'resolved: rejected'}
              </Text>
            </View>
          ) : showDisputeForm ? (
            <View>
              <Text style={styles.sectionTitle}>What went wrong?</Text>
              <TextInput
                style={styles.textInput}
                placeholder="e.g. material never arrived, quality issue, wrong quantity"
                placeholderTextColor="#6b7280"
                value={disputeReason}
                onChangeText={setDisputeReason}
                multiline
              />
              <View style={styles.disputeButtonRow}>
                <TouchableOpacity
                  style={styles.disputeSubmitButton}
                  onPress={handleSubmitDispute}
                  disabled={submittingDispute || !disputeReason.trim()}
                >
                  {submittingDispute ? (
                    <ActivityIndicator color="#06120d" />
                  ) : (
                    <Text style={styles.payButtonText}>Submit</Text>
                  )}
                </TouchableOpacity>
                <TouchableOpacity style={styles.disputeCancelButton} onPress={() => setShowDisputeForm(false)}>
                  <Text style={styles.disputeCancelText}>Cancel</Text>
                </TouchableOpacity>
              </View>
            </View>
          ) : (
            <TouchableOpacity style={styles.reportButton} onPress={() => setShowDisputeForm(true)}>
              <Ionicons name="flag-outline" size={16} color="#f87171" />
              <Text style={styles.reportButtonText}>Report a problem</Text>
            </TouchableOpacity>
          )}
        </View>
      )}
    </ScrollView>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#111827',
  },
  centered: {
    flex: 1,
    backgroundColor: '#111827',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  errorText: {
    color: '#f87171',
    fontSize: 15,
    textAlign: 'center',
    marginTop: 12,
    marginBottom: 16,
  },
  errorTextSmall: {
    color: '#f87171',
    fontSize: 13,
    marginTop: 10,
    textAlign: 'center',
  },
  retryButton: {
    backgroundColor: '#1f2937',
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
  },
  retryButtonText: {
    color: '#ffffff',
    fontWeight: 'bold',
  },
  header: {
    padding: 24,
    paddingBottom: 12,
  },
  orderId: {
    fontSize: 26,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 10,
  },
  badgeRow: {
    flexDirection: 'row',
    gap: 8,
  },
  statusBadge: {
    backgroundColor: '#1f2937',
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
    marginRight: 8,
  },
  statusBadgeText: {
    color: '#e5e7eb',
    fontSize: 11,
    fontWeight: 'bold',
  },
  badgePaid: { backgroundColor: '#065f46' },
  badgeUnpaid: { backgroundColor: '#78350f' },
  badgeRefunded: { backgroundColor: '#1e3a5f' },
  card: {
    backgroundColor: '#1f2937',
    borderRadius: 12,
    padding: 18,
    marginHorizontal: 16,
    marginBottom: 16,
  },
  materialRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  materialName: {
    color: '#ffffff',
    fontSize: 16,
    fontWeight: 'bold',
  },
  materialQty: {
    color: '#9ca3af',
    fontSize: 13,
    marginTop: 2,
  },
  partiesRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#111827',
    borderRadius: 8,
    padding: 12,
    marginBottom: 14,
  },
  partyLabel: {
    color: '#6b7280',
    fontSize: 10,
    fontWeight: 'bold',
    letterSpacing: 0.5,
    marginBottom: 2,
  },
  partyName: {
    color: '#ffffff',
    fontSize: 13,
    fontWeight: '600',
  },
  amountRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderTopWidth: 1,
    borderTopColor: '#374151',
    paddingTop: 12,
  },
  amountLabel: {
    color: '#9ca3af',
    fontSize: 13,
  },
  amountValue: {
    color: '#10b981',
    fontSize: 17,
    fontWeight: 'bold',
  },
  co2Note: {
    color: '#6ee7b7',
    fontSize: 12,
    marginTop: 12,
  },
  payButton: {
    backgroundColor: '#10b981',
    borderRadius: 8,
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  payButtonDisabled: {
    opacity: 0.7,
  },
  payButtonText: {
    color: '#06120d',
    fontSize: 15,
    fontWeight: 'bold',
  },
  reportButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  reportButtonText: {
    color: '#f87171',
    fontSize: 14,
    fontWeight: '600',
    marginLeft: 8,
  },
  disputeNotice: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  disputeNoticeText: {
    color: '#e5e7eb',
    fontSize: 13,
    marginLeft: 8,
  },
  sectionTitle: {
    color: '#ffffff',
    fontSize: 14,
    fontWeight: 'bold',
    marginBottom: 10,
  },
  textInput: {
    backgroundColor: '#111827',
    color: '#ffffff',
    borderRadius: 8,
    padding: 12,
    fontSize: 13,
    minHeight: 70,
    textAlignVertical: 'top',
    marginBottom: 12,
  },
  disputeButtonRow: {
    flexDirection: 'row',
  },
  disputeSubmitButton: {
    flex: 1,
    backgroundColor: '#10b981',
    borderRadius: 8,
    paddingVertical: 12,
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: 10,
  },
  disputeCancelButton: {
    paddingVertical: 12,
    paddingHorizontal: 16,
    alignItems: 'center',
    justifyContent: 'center',
  },
  disputeCancelText: {
    color: '#9ca3af',
    fontSize: 14,
  },
});

export default OrderDetailScreen;
