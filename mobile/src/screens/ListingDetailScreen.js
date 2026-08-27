/**
 * Listing Detail Screen for Mobile App
 */

import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

const ListingDetailScreen = ({ route }) => {
  return (
    <View style={styles.container}>
      <Text style={styles.title}>Listing Details</Text>
      <Text style={styles.subtitle}>View detailed listing information</Text>
    </View>
  );
};

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#111827',
    padding: 24,
  },
  title: {
    fontSize: 28,
    fontWeight: 'bold',
    color: '#ffffff',
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 16,
    color: '#9ca3af',
  },
});

export default ListingDetailScreen;