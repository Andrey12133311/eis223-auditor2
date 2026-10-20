package ru.eis223.auditor;

import java.net.URI;

/** Only our HTTPS origin is allowed inside the authenticated web view. */
public final class UrlPolicy {
    public static final String BASE = "https://web-production-1c2ea.up.railway.app/";
    public static final String HOST = "web-production-1c2ea.up.railway.app";
    private UrlPolicy() { }

    public static boolean trusted(String value) {
        try {
            URI u = new URI(value);
            return "https".equalsIgnoreCase(u.getScheme())
                && HOST.equalsIgnoreCase(u.getHost())
                && u.getRawUserInfo() == null && (u.getPort() == -1 || u.getPort() == 443);
        } catch (Exception e) { return false; }
    }

    public static boolean external(String value) {
        try {
            URI u = new URI(value);
            String s = u.getScheme();
            return ("https".equalsIgnoreCase(s) && u.getHost() != null && u.getRawUserInfo() == null)
                || "mailto".equalsIgnoreCase(s) || "tel".equalsIgnoreCase(s);
        } catch (Exception e) { return false; }
    }

    public static String safeName(String value) {
        if (value == null || value.trim().isEmpty()) return "Документ";
        value = value.replaceAll("[\\\\/\\p{Cntrl}:*?\"<>|]", "_").trim();
        while (value.startsWith(".")) value = value.substring(1);
        if (value.length() > 160) {
            int dot = value.lastIndexOf('.');
            String suffix = dot > 0 && value.length() - dot < 12 ? value.substring(dot) : "";
            value = value.substring(0, 150) + suffix;
        }
        return value.isEmpty() ? "Документ" : value;
    }
}
