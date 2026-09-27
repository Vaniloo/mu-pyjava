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
import javax.swing.JButton;
import javax.swing.JFrame;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
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
            smoke(repository, workspace, allowWrite, allowCommand, ledger, smoke);
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
                              boolean allowCommand, String ledger, String prompt) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        try (var client = new BackendClient(repository, workspace, allowWrite, allowCommand, ledger, event -> {
            if (event.kind().equals("assistant") || event.kind().equals("error") ||
                    event.kind().equals("judge") || event.kind().equals("tool"))
                System.out.println(event.kind() + ": " + event.text());
            if (event.kind().equals("done") || event.kind().equals("stopped")) done.countDown();
        })) {
            client.send(prompt);
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
        var input = new JTextArea(3, 50);
        input.setLineWrap(true);
        var send = new JButton("Send");
        var bottom = new JPanel(new BorderLayout(8, 8));
        bottom.add(new JScrollPane(input), BorderLayout.CENTER);
        bottom.add(send, BorderLayout.EAST);
        frame.add(new JScrollPane(transcript), BorderLayout.CENTER);
        frame.add(bottom, BorderLayout.SOUTH);
        frame.setPreferredSize(new Dimension(760, 560));
        frame.pack();
        frame.setLocationRelativeTo(null);

        try {
            var client = new BackendClient(repository, workspace, allowWrite, allowCommand, ledger, event ->
                SwingUtilities.invokeLater(() -> {
                    if (event.kind().equals("done")) {
                        send.setEnabled(true);
                        return;
                    }
                    if (event.kind().equals("stopped")) send.setEnabled(false);
                    transcript.append("[" + event.kind() + "] " + event.text() + "\n\n");
                })
            );
            send.addActionListener(action -> {
                String text = input.getText().trim();
                if (text.isEmpty()) return;
                input.setText("");
                send.setEnabled(false);
                transcript.append("[you] " + text + "\n\n");
                try {
                    client.send(text);
                } catch (Exception error) {
                    transcript.append("[error] " + error.getMessage() + "\n\n");
                    send.setEnabled(true);
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
        }
    }
}
