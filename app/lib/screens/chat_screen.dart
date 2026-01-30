import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown/flutter_markdown.dart';
import 'package:provider/provider.dart';

import '../core/theme.dart';
import '../services/ai_service.dart';
import '../services/chat_service.dart';

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _controller = TextEditingController();
  final _scrollController = ScrollController();
  final _focusNode = FocusNode();

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    _controller.clear();
    HapticFeedback.lightImpact();
    _scrollToBottom();
    await context.read<ChatService>().sendMessage(text);
    _scrollToBottom();
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<ChatService>(
      builder: (context, chat, _) {
        final ai = context.read<AiService>();
        final hasKey = ai.hasApiKey;

        return Scaffold(
          backgroundColor: WhoopTheme.background,
          appBar: AppBar(
            backgroundColor: Colors.transparent,
            elevation: 0,
            title: GestureDetector(
              onTap: () => _showConversationHistory(chat),
              child: const Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text('AI Coach',
                      style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
                  SizedBox(width: 6),
                  Icon(Icons.expand_more, size: 20, color: WhoopTheme.textSecondary),
                ],
              ),
            ),
            actions: [
              IconButton(
                icon: const Icon(Icons.add, color: WhoopTheme.primary),
                onPressed: () {
                  chat.newConversation();
                  HapticFeedback.lightImpact();
                },
                tooltip: 'New Chat',
              ),
              IconButton(
                icon: const Icon(Icons.tune, color: WhoopTheme.textSecondary),
                onPressed: () => _showDataSettings(chat),
                tooltip: 'Data Settings',
              ),
            ],
          ),
          body: SafeArea(
            child: Column(
              children: [
                Expanded(
                  child: chat.messages.isEmpty
                      ? _buildEmptyState(hasKey)
                      : _buildMessageList(chat),
                ),
                if (chat.error != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                    child: Text(chat.error!,
                        style: const TextStyle(color: WhoopTheme.error, fontSize: 12),
                        maxLines: 2, overflow: TextOverflow.ellipsis),
                  ),
                _buildInput(chat, hasKey),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _buildEmptyState(bool hasKey) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.auto_awesome,
                size: 48, color: WhoopTheme.primary.withValues(alpha: 0.3)),
            const SizedBox(height: 16),
            const Text('AI Coach',
                style: TextStyle(
                    color: WhoopTheme.textPrimary,
                    fontSize: 20,
                    fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            Text(
              hasKey
                  ? 'Ask about your recovery, sleep, strain, or training recommendations.'
                  : 'Set your OpenRouter API key in Settings to start chatting.',
              textAlign: TextAlign.center,
              style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 14),
            ),
            if (hasKey) ...[
              const SizedBox(height: 24),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                alignment: WrapAlignment.center,
                children: [
                  _suggestionChip('How is my recovery today?'),
                  _suggestionChip('Analyze my sleep this week'),
                  _suggestionChip('Should I train hard today?'),
                  _suggestionChip('What are my HRV trends?'),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _suggestionChip(String text) {
    return GestureDetector(
      onTap: () {
        _controller.text = text;
        _send();
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: WhoopTheme.surfaceContainer,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: WhoopTheme.cardBorder, width: 0.5),
        ),
        child: Text(text,
            style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
      ),
    );
  }

  Widget _buildMessageList(ChatService chat) {
    return ListView.builder(
      controller: _scrollController,
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
      itemCount: chat.messages.length + (chat.loading ? 1 : 0),
      itemBuilder: (_, i) {
        if (i == chat.messages.length && chat.loading) {
          return _buildTypingIndicator();
        }
        final msg = chat.messages[i];
        return _buildBubble(msg);
      },
    );
  }

  /// Convert markdown tables to bullet lists for mobile readability.
  String _tablesToBullets(String md) {
    final lines = md.split('\n');
    final out = <String>[];
    var i = 0;
    while (i < lines.length) {
      final line = lines[i].trim();
      if (line.startsWith('|') && line.split('|').length >= 3) {
        final headers = line
            .split('|')
            .map((c) => c.trim())
            .where((c) => c.isNotEmpty)
            .toList();
        i++;
        if (i < lines.length && lines[i].trim().contains('---')) i++;
        while (i < lines.length && lines[i].trim().startsWith('|')) {
          final cells = lines[i]
              .trim()
              .split('|')
              .map((c) => c.trim())
              .where((c) => c.isNotEmpty)
              .toList();
          final parts = <String>[];
          for (var j = 0; j < cells.length && j < headers.length; j++) {
            parts.add('**${headers[j]}**: ${cells[j]}');
          }
          out.add('- ${parts.join(' · ')}');
          i++;
        }
        out.add('');
      } else {
        out.add(lines[i]);
        i++;
      }
    }
    return out.join('\n');
  }

  Widget _buildBubble(ChatMessage msg) {
    final isUser = msg.role == 'user';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        constraints: isUser
            ? BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.8)
            : null,
        margin: EdgeInsets.only(
          top: 4,
          bottom: 4,
          left: isUser ? 48 : 0,
          right: isUser ? 0 : 0,
        ),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: BoxDecoration(
          color: isUser ? WhoopTheme.primary.withValues(alpha: 0.15) : WhoopTheme.surface,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(16),
            topRight: const Radius.circular(16),
            bottomLeft: Radius.circular(isUser ? 16 : 4),
            bottomRight: Radius.circular(isUser ? 4 : 16),
          ),
          border: Border.all(
            color: isUser ? WhoopTheme.primary.withValues(alpha: 0.3) : WhoopTheme.cardBorder,
            width: 0.5,
          ),
        ),
        child: isUser
            ? SelectableText(
                msg.content,
                style: const TextStyle(
                  color: WhoopTheme.primary,
                  fontSize: 14,
                  height: 1.4,
                ),
              )
            : MarkdownBody(
                data: _tablesToBullets(msg.content),
                styleSheet: MarkdownStyleSheet(
                  p: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14, height: 1.5),
                  h1: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 20, fontWeight: FontWeight.bold),
                  h2: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 18, fontWeight: FontWeight.bold),
                  h3: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 16, fontWeight: FontWeight.w600),
                  strong: const TextStyle(color: WhoopTheme.primary, fontWeight: FontWeight.w600),
                  em: const TextStyle(color: WhoopTheme.textSecondary, fontStyle: FontStyle.italic),
                  listBullet: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14),
                  code: TextStyle(
                    color: WhoopTheme.primary,
                    backgroundColor: WhoopTheme.surfaceContainer,
                    fontSize: 13,
                    fontFamily: 'monospace',
                  ),
                  codeblockDecoration: BoxDecoration(
                    color: WhoopTheme.surfaceContainer,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  blockquoteDecoration: BoxDecoration(
                    border: Border(left: BorderSide(color: WhoopTheme.primary, width: 3)),
                  ),
                  blockquotePadding: const EdgeInsets.only(left: 12),
                  tableBorder: TableBorder.all(color: WhoopTheme.cardBorder, width: 0.5),
                  tableHead: const TextStyle(color: WhoopTheme.textPrimary, fontWeight: FontWeight.w600, fontSize: 13),
                  tableBody: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 13),
                  horizontalRuleDecoration: BoxDecoration(
                    border: Border(top: BorderSide(color: WhoopTheme.divider, width: 0.5)),
                  ),
                ),
                selectable: true,
              ),
      ),
    );
  }

  Widget _buildTypingIndicator() {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(top: 4, bottom: 4, right: 48),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        decoration: BoxDecoration(
          color: WhoopTheme.surface,
          borderRadius: const BorderRadius.only(
            topLeft: Radius.circular(16),
            topRight: Radius.circular(16),
            bottomRight: Radius.circular(16),
            bottomLeft: Radius.circular(4),
          ),
          border: Border.all(color: WhoopTheme.cardBorder, width: 0.5),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(
              width: 16, height: 16,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: WhoopTheme.primary.withValues(alpha: 0.6),
              ),
            ),
            const SizedBox(width: 10),
            const Text('Thinking...',
                style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 13)),
          ],
        ),
      ),
    );
  }

  Widget _buildInput(ChatService chat, bool hasKey) {
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
      decoration: BoxDecoration(
        color: WhoopTheme.surface,
        border: Border(top: BorderSide(color: WhoopTheme.cardBorder, width: 0.5)),
      ),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _controller,
              focusNode: _focusNode,
              enabled: hasKey && !chat.loading,
              style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 15),
              maxLines: 4,
              minLines: 1,
              textInputAction: TextInputAction.send,
              onSubmitted: (_) => _send(),
              decoration: InputDecoration(
                hintText: hasKey ? 'Ask anything...' : 'Set API key in Settings',
                hintStyle: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 14),
                filled: true,
                fillColor: WhoopTheme.surfaceContainer,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(20),
                  borderSide: BorderSide.none,
                ),
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              ),
            ),
          ),
          const SizedBox(width: 8),
          GestureDetector(
            onTap: (hasKey && !chat.loading) ? _send : null,
            child: Container(
              width: 40, height: 40,
              decoration: BoxDecoration(
                color: (hasKey && !chat.loading)
                    ? WhoopTheme.primary
                    : WhoopTheme.divider,
                shape: BoxShape.circle,
              ),
              child: Icon(Icons.arrow_upward,
                  color: (hasKey && !chat.loading)
                      ? WhoopTheme.background
                      : WhoopTheme.textSecondary,
                  size: 20),
            ),
          ),
        ],
      ),
    );
  }

  void _showConversationHistory(ChatService chat) {
    showModalBottomSheet(
      context: context,
      backgroundColor: WhoopTheme.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 36, height: 4,
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: WhoopTheme.divider,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            Row(
              children: [
                const Text('Conversations',
                    style: TextStyle(color: WhoopTheme.textPrimary,
                        fontSize: 16, fontWeight: FontWeight.bold)),
                const Spacer(),
                GestureDetector(
                  onTap: () {
                    Navigator.pop(ctx);
                    chat.newConversation();
                  },
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color: WhoopTheme.primary.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Text('New Chat',
                        style: TextStyle(color: WhoopTheme.primary, fontSize: 12,
                            fontWeight: FontWeight.w600)),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (chat.conversations.isEmpty)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Center(child: Text('No conversations yet',
                    style: TextStyle(color: WhoopTheme.textSecondary))),
              )
            else
              ConstrainedBox(
                constraints: BoxConstraints(
                  maxHeight: MediaQuery.of(ctx).size.height * 0.4,
                ),
                child: ListView.builder(
                  shrinkWrap: true,
                  itemCount: chat.conversations.length,
                  itemBuilder: (_, i) {
                    final convo = chat.conversations[i];
                    final selected = chat.current?.id == convo.id;
                    final date = DateTime.fromMillisecondsSinceEpoch(convo.createdAt);
                    final dateStr = _formatDate(date);
                    return ListTile(
                      dense: true,
                      contentPadding: const EdgeInsets.symmetric(horizontal: 4),
                      title: Text(convo.title,
                          maxLines: 1, overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            color: selected ? WhoopTheme.primary : WhoopTheme.textPrimary,
                            fontSize: 14,
                            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                          )),
                      subtitle: Text('$dateStr - ${convo.messages.length} messages',
                          style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 11)),
                      trailing: IconButton(
                        icon: const Icon(Icons.delete_outline, size: 18, color: WhoopTheme.textSecondary),
                        onPressed: () {
                          chat.deleteConversation(convo.id);
                          if (chat.conversations.isEmpty) Navigator.pop(ctx);
                        },
                      ),
                      onTap: () {
                        chat.selectConversation(convo.id);
                        Navigator.pop(ctx);
                        _scrollToBottom();
                      },
                    );
                  },
                ),
              ),
          ],
        ),
      ),
    );
  }

  String _formatDate(DateTime date) {
    final now = DateTime.now();
    final diff = now.difference(date);
    if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
    if (diff.inHours < 24) return '${diff.inHours}h ago';
    if (diff.inDays < 7) return '${diff.inDays}d ago';
    return '${date.day}/${date.month}';
  }

  void _showDataSettings(ChatService chat) {
    showModalBottomSheet(
      context: context,
      backgroundColor: WhoopTheme.surface,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheetState) => Padding(
          padding: EdgeInsets.fromLTRB(16, 16, 16, MediaQuery.of(ctx).viewInsets.bottom + 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 36, height: 4,
                  margin: const EdgeInsets.only(bottom: 16),
                  decoration: BoxDecoration(
                    color: WhoopTheme.divider,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const Text('Data Context',
                  style: TextStyle(color: WhoopTheme.textPrimary,
                      fontSize: 16, fontWeight: FontWeight.bold)),
              const SizedBox(height: 4),
              const Text('Configure what data the AI can access',
                  style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12)),
              const SizedBox(height: 16),

              // Days slider
              Row(
                children: [
                  const Icon(Icons.date_range, color: WhoopTheme.textSecondary, size: 20),
                  const SizedBox(width: 10),
                  const Expanded(child: Text('History days',
                      style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14))),
                  Text('${chat.dataDays}',
                      style: const TextStyle(color: WhoopTheme.primary,
                          fontSize: 14, fontWeight: FontWeight.w600)),
                ],
              ),
              Slider(
                value: chat.dataDays.toDouble(),
                min: 1,
                max: 30,
                divisions: 29,
                activeColor: WhoopTheme.primary,
                inactiveColor: WhoopTheme.divider,
                onChanged: (v) {
                  chat.setDataDays(v.round());
                  setSheetState(() {});
                },
              ),

              // Toggles
              _settingToggle(
                'Include workouts',
                Icons.fitness_center,
                chat.includeWorkouts,
                (v) { chat.setIncludeWorkouts(v); setSheetState(() {}); },
              ),
              _settingToggle(
                'Include sleep stages',
                Icons.bedtime,
                chat.includeSleepStages,
                (v) { chat.setIncludeSleepStages(v); setSheetState(() {}); },
              ),
              _settingToggle(
                'Include stress data',
                Icons.psychology,
                chat.includeStress,
                (v) { chat.setIncludeStress(v); setSheetState(() {}); },
              ),

              const SizedBox(height: 16),
              const Divider(color: WhoopTheme.divider, height: 1),
              const SizedBox(height: 12),

              // System prompt section
              Row(
                children: [
                  const Icon(Icons.code, color: WhoopTheme.textSecondary, size: 20),
                  const SizedBox(width: 10),
                  const Expanded(child: Text('System Prompt',
                      style: TextStyle(color: WhoopTheme.textPrimary, fontSize: 14))),
                  if (chat.customSystemPrompt != null)
                    GestureDetector(
                      onTap: () {
                        chat.setCustomSystemPrompt(null);
                        setSheetState(() {});
                      },
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          color: WhoopTheme.error.withValues(alpha: 0.1),
                          borderRadius: BorderRadius.circular(6),
                        ),
                        child: const Text('Reset',
                            style: TextStyle(color: WhoopTheme.error, fontSize: 11, fontWeight: FontWeight.w600)),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              GestureDetector(
                onTap: () {
                  Navigator.pop(ctx);
                  _showSystemPromptEditor(chat);
                },
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: WhoopTheme.surfaceContainer,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: WhoopTheme.cardBorder, width: 0.5),
                  ),
                  child: Text(
                    chat.customSystemPrompt ?? ChatService.defaultSystemPromptPreview,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: WhoopTheme.textSecondary, fontSize: 12, height: 1.4),
                  ),
                ),
              ),
              const SizedBox(height: 4),
              const Text('Tap to edit. Data JSON is always appended.',
                  style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 10)),
              const SizedBox(height: 8),
            ],
          ),
        ),
      ),
    );
  }

  void _showSystemPromptEditor(ChatService chat) {
    final promptController = TextEditingController(
      text: chat.customSystemPrompt ?? ChatService.defaultSystemPrompt,
    );
    showDialog(
      context: context,
      builder: (ctx) => Dialog.fullscreen(
        backgroundColor: WhoopTheme.background,
        child: Scaffold(
          backgroundColor: WhoopTheme.background,
          appBar: AppBar(
            backgroundColor: Colors.transparent,
            elevation: 0,
            title: const Text('System Prompt', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
            leading: IconButton(
              icon: const Icon(Icons.close),
              onPressed: () => Navigator.pop(ctx),
            ),
            actions: [
              TextButton(
                onPressed: () {
                  promptController.text = ChatService.defaultSystemPrompt;
                },
                child: const Text('Reset', style: TextStyle(color: WhoopTheme.textSecondary)),
              ),
              TextButton(
                onPressed: () {
                  final text = promptController.text.trim();
                  if (text == ChatService.defaultSystemPrompt || text.isEmpty) {
                    chat.setCustomSystemPrompt(null);
                  } else {
                    chat.setCustomSystemPrompt(text);
                  }
                  Navigator.pop(ctx);
                  HapticFeedback.lightImpact();
                },
                child: const Text('Save', style: TextStyle(color: WhoopTheme.primary, fontWeight: FontWeight.w600)),
              ),
            ],
          ),
          body: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'The system prompt sets the AI\'s behavior. Your Whoop data (JSON) is always appended at the end.',
                  style: TextStyle(color: WhoopTheme.textSecondary, fontSize: 12),
                ),
                const SizedBox(height: 12),
                Expanded(
                  child: TextField(
                    controller: promptController,
                    maxLines: null,
                    expands: true,
                    textAlignVertical: TextAlignVertical.top,
                    style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 13, height: 1.6),
                    decoration: InputDecoration(
                      filled: true,
                      fillColor: WhoopTheme.surface,
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: const BorderSide(color: WhoopTheme.cardBorder),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: const BorderSide(color: WhoopTheme.cardBorder),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                        borderSide: const BorderSide(color: WhoopTheme.primary),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _settingToggle(String label, IconData icon, bool value, ValueChanged<bool> onChanged) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          Icon(icon, color: WhoopTheme.textSecondary, size: 20),
          const SizedBox(width: 10),
          Expanded(child: Text(label,
              style: const TextStyle(color: WhoopTheme.textPrimary, fontSize: 14))),
          Switch(
            value: value,
            onChanged: onChanged,
            activeColor: WhoopTheme.primary,
          ),
        ],
      ),
    );
  }
}
