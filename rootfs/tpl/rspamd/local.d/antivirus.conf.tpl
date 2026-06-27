# ClamAV antivirus glue.  Connects to the external clamd sidecar at
# ${CLAMAV_HOST}:${CLAMAV_PORT}.  render-config renders THIS template only when
# CLAMAV_ENABLED is true and CLAMAV_HOST is set; otherwise it writes the
# disabled stanza below (see render-config.sh).  A hit adds CLAM_VIRUS and a
# heavy score.
clamav {
  type = "clamav";
  servers = "${CLAMAV_HOST}:${CLAMAV_PORT}";
  symbol = "CLAM_VIRUS";
  action = "reject";
  scan_mime_parts = true;
  max_size = 20971520;
}
