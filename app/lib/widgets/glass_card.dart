import 'package:flutter/material.dart';

import '../core/theme.dart';

class GlassCard extends StatelessWidget {
  final Widget child;
  final double radius;
  final EdgeInsetsGeometry? padding;
  final EdgeInsetsGeometry? margin;

  const GlassCard({
    super.key,
    required this.child,
    this.radius = 16,
    this.padding,
    this.margin,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: margin ?? EdgeInsets.zero,
      child: Container(
        padding: padding,
        decoration: WhoopTheme.cardDecoration(radius: radius),
        child: child,
      ),
    );
  }
}
