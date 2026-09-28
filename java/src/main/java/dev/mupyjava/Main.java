package dev.mupyjava;

import java.awt.BorderLayout;
import java.awt.Dimension;
import java.awt.Font;
import java.awt.event.WindowAdapter;
import java.awt.event.WindowEvent;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import javax.swing.JButton;
import javax.swing.JFrame;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTabbedPane;
import javax.swing.JTextArea;
import javax.swing.SwingUtilities;

/** Minimal Swing shell. The Python process owns all agent state. */
public final class Main {
    private Main() {}

    public static void main(String[] args) throws Exception {
        Path repository = Path.of("").toAbsolutePath();
        Path workspace = valueAfter(args, "--workspace") == null
                ? repository : Path.of(valueAfter(args, "--workspace"));
        boolean allowWrite = Arrays.asList(args).contains("--allow-write");
        boolean allowCommand = Arrays.asList(args).contains("--allow-command");
        String ledger = valueAfter(args, "--ledger");
        String smoke = valueAfter(args, "--smoke");
        if (smoke != null) {
            String smokeApproval = valueAfter(args, "--smoke-approval");
            if (smokeApproval == null) smokeApproval = "deny";
            if (!smokeApproval.equals("deny") && !smokeApproval.equals("once"))
                throw new IllegalArgumentException("--smoke-approval must be deny or once");
            boolean cancelOnUpdate = Arrays.asList(args).contains("--smoke-cancel-on-update");
            smoke(repository, workspace, allowWrite, allowCommand, ledger, smoke, smokeApproval, cancelOnUpdate);
            return;
        }
        SwingUtilities.invokeLater(() -> show(repository, workspace, allowWrite, allowCommand, ledger));
    }

    private static String valueAfter(String[] args, String name) {
        for (int index = 0; index + 1 < args.length; index++) {
            if (args[index].equals(name)) return args[index + 1];
        }
        return null;
    }

    private static void smoke(Path repository, Path workspace, boolean allowWrite,
                              boolean allowCommand, String ledger, String prompt,
                              String approvalAnswer, boolean cancelOnUpdate) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<BackendClient> clientRef = new AtomicReference<>();
        AtomicReference<String> turnId = new AtomicReference<>();
        AtomicBoolean cancelled = new AtomicBoolean();
        try (var client = new BackendClient(repository, workspace, allowWrite, allowCommand, ledger, event -> {
            if (event.kind().equals("approval.request")) {
                try {
                    var request = BackendClient.parseApproval(event.text());
                    clientRef.get().sendApproval(request.approvalId(), approvalAnswer);
                } catch (Exception error) {
                    System.out.println("error: " + error.getMessage());
                    clientRef.get().close();
                }
            }
            if (event.kind().equals("tool.update") && cancelOnUpdate && cancelled.compareAndSet(false, true)) {
                try {
                    clientRef.get().cancel(turnId.get());
                } catch (Exception error) {
                    System.out.println("error: " + error.getMessage());
                }
            }
            if (event.kind().equals("assistant") || event.kind().equals("error") ||
                    event.kind().equals("judge") || event.kind().equals("tool") ||
                    event.kind().equals("history.judge") || event.kind().equals("cancelled") ||
                    event.kind().equals("tool.update") || event.kind().equals("tool.change") ||
                    event.kind().equals("tool.detail"))
                System.out.println(event.kind() + ": " + event.text());
            if (event.kind().equals("done") || event.kind().equals("stopped")) done.countDown();
        })) {
            clientRef.set(client);
            client.requestHistory();
            turnId.set(client.send(prompt));
            if (!done.await(90, TimeUnit.SECONDS)) throw new IllegalStateException("Backend did not finish");
        }
    }

    private static void show(Path repository, Path workspace, boolean allowWrite, boolean allowCommand, String ledger) {
        var frame = new JFrame("mu-pyjava");
        frame.setDefaultCloseOperation(JFrame.DISPOSE_ON_CLOSE);
        var transcript = new JTextArea();
        transcript.setEditable(false);
        transcript.setLineWrap(true);
        transcript.setWrapStyleWord(true);
        transcript.setFont(new Font(Font.MONOSPACED, Font.PLAIN, 13));
        var judgments = new JTextArea();
        judgments.setEditable(false);
        judgments.setLineWrap(true);
        judgments.setWrapStyleWord(true);
        judgments.setFont(new Font(Font.MONOSPACED, Font.PLAIN, 13));
        var tabs = new JTabbedPane();
        tabs.addTab("Conversation", new JScrollPane(transcript));
        tabs.addTab("Judgments", new JScrollPane(judgments));
        var input = new JTextArea(3, 50);
        input.setLineWrap(true);
        var send = new JButton("Send");
        var stop = new JButton("Stop");
        var newSession = new JButton("New session");
        send.setEnabled(false);
        stop.setEnabled(false);
        newSession.setEnabled(false);
        var actions = new JPanel(new BorderLayout(4, 4));
        actions.add(send, BorderLayout.NORTH);
        actions.add(stop, BorderLayout.CENTER);
        actions.add(newSession, BorderLayout.SOUTH);
        var bottom = new JPanel(new BorderLayout(8, 8));
        bottom.add(new JScrollPane(input), BorderLayout.CENTER);
        bottom.add(actions, BorderLayout.EAST);
        frame.add(tabs, BorderLayout.CENTER);
        frame.add(bottom, BorderLayout.SOUTH);
        frame.setPreferredSize(new Dimension(760, 560));
        frame.pack();
        frame.setLocationRelativeTo(null);

        try {
            AtomicReference<BackendClient> clientRef = new AtomicReference<>();
            AtomicReference<String> activeRequest = new AtomicReference<>();
            var client = new BackendClient(repository, workspace, allowWrite, allowCommand, ledger, event ->
                SwingUtilities.invokeLater(() -> {
                    if (event.kind().equals("approval.request")) {
                        try {
                            var request = BackendClient.parseApproval(event.text());
                            String answer = askApproval(frame, request);
                            clientRef.get().sendApproval(request.approvalId(), answer);
                        } catch (Exception error) {
                            transcript.append("[error] Approval failed: " + error.getMessage() + "\n\n");
                            clientRef.get().close();
                        }
                        return;
                    }
                    if (event.kind().equals("session.info")) {
                        frame.setTitle("mu-pyjava · session " + event.text().substring(0, 8));
                        return;
                    }
                    if (event.kind().equals("history.transcript")) {
                        appendBounded(transcript, event.text());
                        return;
                    }
                    if (event.kind().equals("history.judge") || event.kind().equals("judge.detail")) {
                        appendBounded(judgments, event.text());
                        return;
                    }
                    if (event.kind().equals("tool.update")) {
                        appendBounded(transcript, event.text());
                        return;
                    }
                    if (event.kind().equals("history.done")) {
                        send.setEnabled(true);
                        newSession.setEnabled(true);
                        return;
                    }
                    if (event.kind().equals("approval.resolved")) return;
                    if (event.kind().equals("done")) {
                        if (event.id().equals(activeRequest.get())) activeRequest.set(null);
                        stop.setEnabled(false);
                        send.setEnabled(true);
                        newSession.setEnabled(true);
                        return;
                    }
                    if (event.kind().equals("stopped")) {
                        send.setEnabled(false);
                        stop.setEnabled(false);
                        newSession.setEnabled(false);
                    }
                    appendBounded(transcript, "[" + event.kind() + "] " + event.text() + "\n\n");
                })
            );
            clientRef.set(client);
            client.requestHistory();
            send.addActionListener(action -> {
                String text = input.getText().trim();
                if (text.isEmpty()) return;
                input.setText("");
                send.setEnabled(false);
                stop.setEnabled(true);
                newSession.setEnabled(false);
                appendBounded(transcript, "[you] " + text + "\n\n");
                try {
                    activeRequest.set(client.send(text));
                } catch (Exception error) {
                    appendBounded(transcript, "[error] " + error.getMessage() + "\n\n");
                    send.setEnabled(true);
                    stop.setEnabled(false);
                    newSession.setEnabled(true);
                }
            });
            stop.addActionListener(action -> {
                String id = activeRequest.get();
                if (id == null) return;
                stop.setEnabled(false);
                try {
                    client.cancel(id);
                } catch (Exception error) {
                    appendBounded(transcript, "[error] " + error.getMessage() + "\n\n");
                }
            });
            newSession.addActionListener(action -> {
                send.setEnabled(false);
                newSession.setEnabled(false);
                try {
                    client.newSession();
                    transcript.setText("");
                    judgments.setText("");
                } catch (Exception error) {
                    appendBounded(transcript, "[error] " + error.getMessage() + "\n\n");
                    send.setEnabled(true);
                    newSession.setEnabled(true);
                }
            });
            frame.addWindowListener(new WindowAdapter() {
                @Override public void windowClosed(WindowEvent event) { client.close(); }
            });
            frame.setVisible(true);
        } catch (Exception error) {
            transcript.append("[error] Could not start Python backend: " + error.getMessage());
            frame.setVisible(true);
            send.setEnabled(false);
            stop.setEnabled(false);
            newSession.setEnabled(false);
        }
    }

    private static String askApproval(JFrame frame, BackendClient.ApprovalRequest request) {
        var details = new JTextArea(request.summary() + "\n\n" + request.preview(), 22, 76);
        details.setEditable(false);
        details.setLineWrap(true);
        details.setWrapStyleWord(true);
        details.setCaretPosition(0);
        Object[] options = request.sessionOption()
                ? new Object[] {"Deny", "Allow once", "Allow for this file in this session"}
                : new Object[] {"Deny", "Allow once"};
        int selected = JOptionPane.showOptionDialog(frame, new JScrollPane(details),
                "Approve tool action", JOptionPane.DEFAULT_OPTION, JOptionPane.QUESTION_MESSAGE,
                null, options, options[0]);
        if (selected == 1) return "once";
        if (selected == 2 && request.sessionOption()) return "session";
        return "deny";
    }

    private static void appendBounded(JTextArea area, String text) {
        area.append(text);
        int extra = area.getDocument().getLength() - 120_000;
        if (extra > 0) area.replaceRange("", 0, Math.min(extra + 20_000, area.getDocument().getLength()));
    }
}
