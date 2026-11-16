FROM nginx:1.30.6-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951
COPY --chmod=0644 nginx.conf /templates/nginx.conf
COPY --chmod=0555 entrypoint.sh /opt/entrypoint.sh
STOPSIGNAL SIGQUIT
ENTRYPOINT ["/opt/entrypoint.sh"]
