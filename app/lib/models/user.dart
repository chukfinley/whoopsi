class User {
  final String id;
  final String email;
  final String firstName;
  final String lastName;
  final String? avatarUrl;

  const User({
    required this.id,
    required this.email,
    required this.firstName,
    required this.lastName,
    this.avatarUrl,
  });

  String get fullName => '$firstName $lastName';

  factory User.fromJson(Map<String, dynamic> json) => User(
        id: '${json['user_id'] ?? json['id'] ?? ''}',
        email: json['email'] as String? ?? '',
        firstName: json['first_name'] as String? ?? '',
        lastName: json['last_name'] as String? ?? '',
        avatarUrl: json['avatar_url'] as String?,
      );
}
