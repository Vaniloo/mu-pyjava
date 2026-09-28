package dev.mupyjava;

import java.awt.BorderLayout;
import java.awt.Dimension;
import java.awt.event.WindowAdapter;
import java.awt.event.WindowEvent;
import java.util.function.BiConsumer;
import javax.swing.*;

/** Browse saved conversations and completed turns. All operations stay on the EDT. */
public final class SessionBrowser {
    private final JDialog dialog;
    private final BackendClient client;
    private final DefaultListModel<BackendClient.SessionItem> sessions = new DefaultListModel<>();
    private final DefaultListModel<BackendClient.SessionPoint> points = new DefaultListModel<>();
    private final JList<BackendClient.SessionItem> sessionList = new JList<>(sessions);
    private final JList<BackendClient.SessionPoint> pointList = new JList<>(points);
    private final JLabel status = new JLabel("Loading conversations…");
    private final JButton select = new JButton("Continue here");
    private final JButton fork = new JButton("Copy as new conversation");
    private String sessionRequest;
    private String pointRequest;

    public SessionBrowser(JFrame owner, BackendClient client, BiConsumer<String, String> onSelect,
                          BiConsumer<String, String> onFork, Runnable onClose) {
        this.client = client;
        dialog = new JDialog(owner, "Saved conversations", false);
        dialog.setDefaultCloseOperation(JDialog.DISPOSE_ON_CLOSE);
        sessionList.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);
        pointList.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);
        sessionList.setCellRenderer(renderer(true));
        pointList.setCellRenderer(renderer(false));
        var left = new JPanel(new BorderLayout());
        left.add(new JLabel("Conversations (latest 200)"), BorderLayout.NORTH);
        left.add(new JScrollPane(sessionList));
        var right = new JPanel(new BorderLayout());
        right.add(new JLabel("Beginning and completed turns (latest 200)"), BorderLayout.NORTH);
        right.add(new JScrollPane(pointList));
        var split = new JSplitPane(JSplitPane.HORIZONTAL_SPLIT, left, right);
        split.setResizeWeight(0.5);
        var footer = new JPanel(new BorderLayout());
        footer.add(new JLabel("Files keep their current contents. Tool permissions will be requested again."), BorderLayout.NORTH);
        footer.add(status, BorderLayout.CENTER);
        var buttons = new JPanel();
        buttons.add(select);
        buttons.add(fork);
        footer.add(buttons, BorderLayout.SOUTH);
        dialog.add(split);
        dialog.add(footer, BorderLayout.SOUTH);
        dialog.setPreferredSize(new Dimension(900, 480));
        dialog.pack();
        dialog.setLocationRelativeTo(owner);
        select.setEnabled(false);
        fork.setEnabled(false);
        sessionList.addListSelectionListener(event -> {
            if (event.getValueIsAdjusting()) return;
            points.clear();
            pointRequest = null;
            select.setEnabled(false);
            fork.setEnabled(false);
            var item = sessionList.getSelectedValue();
            if (item == null) return;
            try { pointRequest = client.requestPoints(item.sessionId()); status.setText("Loading completed turns…"); }
            catch (Exception error) { status.setText(error.getMessage()); }
        });
        pointList.addListSelectionListener(event -> {
            boolean ready = pointList.getSelectedValue() != null;
            select.setEnabled(ready);
            fork.setEnabled(ready);
        });
        select.addActionListener(event -> choose(onSelect));
        fork.addActionListener(event -> choose(onFork));
        dialog.addWindowListener(new WindowAdapter() {
            @Override public void windowClosed(WindowEvent event) { onClose.run(); }
        });
    }

    private static ListCellRenderer<Object> renderer(boolean isSession) {
        return (list, value, index, selected, focus) -> {
            String title;
            String at;
            if (isSession) {
                var item = (BackendClient.SessionItem) value;
                title = (item.parentSessionId().isEmpty() ? "" : "↳ ") + item.title();
                at = item.updatedAt();
            } else {
                var item = (BackendClient.SessionPoint) value;
                title = item.title(); at = item.at();
            }
            // JLabel HTML is disabled: prompts are plain text, including '<html>'.
            var label = new JLabel();
            label.putClientProperty("html.disable", true);
            label.setText(at.substring(0, Math.min(19, at.length())).replace('T', ' ') + " · " + title);
            label.setOpaque(true);
            label.setBackground(selected ? list.getSelectionBackground() : list.getBackground());
            label.setForeground(selected ? list.getSelectionForeground() : list.getForeground());
            label.setBorder(BorderFactory.createEmptyBorder(5, 5, 5, 5));
            return label;
        };
    }

    private void choose(BiConsumer<String, String> action) {
        var item = sessionList.getSelectedValue();
        var point = pointList.getSelectedValue();
        if (item == null || point == null) return;
        action.accept(item.sessionId(), point.pointId());
        dialog.dispose();
    }
    public void show() {
        try { sessionRequest = client.requestSessions(); }
        catch (Exception error) { status.setText(error.getMessage()); }
        dialog.setVisible(true);
    }
    public void close() { dialog.dispose(); }
    public void accept(BackendClient.Event event) {
        try {
            if (event.id().equals(sessionRequest)) {
                if (event.kind().equals("session.item")) sessions.addElement(BackendClient.parseSessionItem(event.text()));
                else if (event.kind().equals("session.list_done")) {
                    status.setText(sessions.isEmpty() ? "No saved conversations" : "Choose a conversation and a completed turn");
                    if (!sessions.isEmpty()) sessionList.setSelectedIndex(0);
                } else if (event.kind().equals("error")) status.setText(event.text());
            } else if (event.id().equals(pointRequest)) {
                if (event.kind().equals("session.point")) points.addElement(BackendClient.parseSessionPoint(event.text()));
                else if (event.kind().equals("session.points_done")) {
                    status.setText("Choose where to continue or copy");
                    if (!points.isEmpty()) pointList.setSelectedIndex(points.size() - 1);
                } else if (event.kind().equals("error")) status.setText(event.text());
            }
        } catch (Exception error) { status.setText("Could not read saved conversation: " + error.getMessage()); }
    }
}
